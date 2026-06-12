"""Processing endpoints: start pipeline and check status."""
import asyncio
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.pipeline import cancel
from backend.storage.database import get_db
from backend.storage.models import ProcessingRun, User

router = APIRouter()

# Тяжёлый пайплайн выполняется по одному: параллельные прогоны дрались бы за
# GPU/Ollama. Остальные запуски встают в FIFO-очередь и стартуют автоматически.
# Флаг и очередь живут в памяти процесса бэкенда; после рестарта «осиротевшие»
# running/pending в БД помечаются как failed при следующем старте.
_active_lock = threading.Lock()
_active_run_id: int | None = None
_queue: list[tuple[int, str]] = []  # (run_id, filepath) в порядке поступления


@router.post("/process")
async def start_processing(
    request: Request,
    filepath: str,
    filename: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Запустить обработку или поставить её в очередь, если слот занят."""
    global _active_run_id
    _ = request

    start_now = False
    with _active_lock:
        if _active_run_id is None:
            _active_run_id = -1  # резервируем слот до получения настоящего id
            start_now = True

    if start_now:
        try:
            # Слот свободен и очередь пуста — любые running/pending в БД
            # осиротели после рестарта бэкенда, помечаем их как failed.
            await db.execute(
                update(ProcessingRun)
                .where(ProcessingRun.status.in_(["running", "pending"]))
                .values(status="failed", error_message="Прервано: запущена новая обработка")
            )
            run = ProcessingRun(
                user_id=current_user.id,
                filename=filename,
                status="running",
                current_step="Загрузка файла",
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
        except Exception:
            with _active_lock:
                _active_run_id = None
            raise

        with _active_lock:
            _active_run_id = run.id
        asyncio.create_task(_run_pipeline(run.id, filepath))
        return {"run_id": run.id, "status": "running", "queue_position": 0}

    # Слот занят — ставим в очередь.
    run = ProcessingRun(
        user_id=current_user.id,
        filename=filename,
        status="pending",
        current_step="В очереди на обработку",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    with _active_lock:
        _queue.append((run.id, filepath))
        position = len(_queue)

    return {"run_id": run.id, "status": "pending", "queue_position": position}


async def _start_next_from_queue() -> None:
    """Запустить следующий прогон из очереди (пропуская отменённые)."""
    global _active_run_id
    from backend.storage.database import AsyncSessionLocal

    while True:
        with _active_lock:
            if _active_run_id is not None or not _queue:
                return
            next_id, next_path = _queue.pop(0)
            _active_run_id = next_id

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ProcessingRun).where(ProcessingRun.id == next_id))
            run = result.scalar_one_or_none()
            if not run or run.status != "pending":
                # Отменён, пока ждал в очереди — освобождаем слот и берём следующего.
                with _active_lock:
                    if _active_run_id == next_id:
                        _active_run_id = None
                continue
            run.status = "running"
            run.current_step = "Запуск обработки"
            await session.commit()

        asyncio.create_task(_run_pipeline(next_id, next_path))
        return


async def _run_pipeline(run_id: int, filepath: str):
    """Run the full processing pipeline in a background thread."""
    global _active_run_id
    from backend.pipeline.orchestrator import run_pipeline

    try:
        await asyncio.to_thread(run_pipeline, run_id, filepath)
    except Exception as exc:
        from backend.storage.database import get_sync_db

        session = get_sync_db()
        try:
            run = session.get(ProcessingRun, run_id)
            if run:
                run.status = "failed"
                run.error_message = str(exc)[:1000]
                session.commit()
        finally:
            session.close()
    finally:
        with _active_lock:
            if _active_run_id == run_id:
                _active_run_id = None
        await _start_next_from_queue()


@router.post("/processing/{run_id}/cancel")
async def cancel_processing(
    run_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Остановить выполняющуюся обработку (кооперативно, ближайшим шагом)."""
    result = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    if current_user.role != "admin" and run.user_id != current_user.id:
        raise HTTPException(403, "Можно останавливать только свои обработки")

    if run.status == "pending":
        # Ещё не стартовал — просто убираем из очереди.
        with _active_lock:
            for index, item in enumerate(_queue):
                if item[0] == run_id:
                    _queue.pop(index)
                    break
        run.status = "failed"
        run.error_message = "Отменено в очереди"
        run.current_step = "Отменено"
        await db.commit()
        return {"ok": True, "status": "cancelled"}

    if run.status != "running":
        raise HTTPException(400, "Обработка уже завершена")

    cancel.request_cancel(run_id)
    # Сразу отражаем остановку в БД: pipeline доостановится на ближайшем шаге.
    run.status = "failed"
    run.error_message = "Остановлено пользователем"
    run.current_step = "Обработка прервана"
    await db.commit()
    return {"ok": True, "status": "cancelling"}


@router.get("/processing/latest")
async def get_latest_run(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the latest run for the current user, if any."""
    filters = [ProcessingRun.status.in_(["completed", "running", "pending"])]
    if current_user.role != "admin":
        filters.append(ProcessingRun.user_id == current_user.id)

    result = await db.execute(
        select(ProcessingRun)
        .where(*filters)
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if not run:
        return {"run_id": None}

    return {
        "run_id": run.id,
        "status": run.status,
        "filename": run.filename,
    }


@router.get("/processing/{run_id}/status")
async def get_status(run_id: int, db: AsyncSession = Depends(get_db)):
    """Get processing status and progress (для очереди — позиция и активный файл)."""
    result = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")

    payload = {
        "run_id": run.id,
        "filename": run.filename,
        "status": run.status,
        "progress": run.progress,
        "current_step": run.current_step,
        "total_records": run.total_records,
        "problem_count": run.problem_count,
        "cluster_count": run.cluster_count,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error_message": run.error_message,
    }

    if run.status == "pending":
        with _active_lock:
            queued_ids = [item[0] for item in _queue]
            active_id = _active_run_id
        if run.id in queued_ids:
            payload["queue_position"] = queued_ids.index(run.id) + 1
            payload["queue_size"] = len(queued_ids)
        if active_id and active_id > 0:
            active_q = await db.execute(select(ProcessingRun).where(ProcessingRun.id == active_id))
            active = active_q.scalar_one_or_none()
            if active:
                payload["active_run"] = {
                    "run_id": active.id,
                    "filename": active.filename,
                    "progress": active.progress,
                }

    return payload
