"""Processing endpoints: start pipeline and check status."""
import asyncio
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.storage.database import get_db
from backend.storage.models import ProcessingRun, User

router = APIRouter()

# Одновременно допускается только один процесс обработки. Флаг живёт в памяти
# процесса бэкенда: пока он занят — новые запуски отклоняются. После рестарта
# бэкенда флаг сбрасывается, а «осиротевшие» running-прогоны в БД помечаются
# как failed при следующем старте.
_active_lock = threading.Lock()
_active_run_id: int | None = None


@router.post("/process")
async def start_processing(
    request: Request,
    filepath: str,
    filename: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start background processing pipeline (только один процесс за раз)."""
    global _active_run_id

    with _active_lock:
        if _active_run_id is not None:
            raise HTTPException(
                409,
                f"Обработка уже выполняется (прогон #{_active_run_id}). "
                "Дождитесь завершения текущего процесса.",
            )
        _active_run_id = -1  # резервируем слот до получения настоящего id

    try:
        # Подчищаем зависшие прогоны прошлой сессии: в памяти активного нет,
        # значит любые running в БД — осиротевшие, помечаем их как failed.
        await db.execute(
            update(ProcessingRun)
            .where(ProcessingRun.status == "running")
            .values(status="failed", error_message="Прервано: запущена новая обработка")
        )

        _ = request
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

    return {"run_id": run.id, "status": "running"}


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


@router.get("/processing/latest")
async def get_latest_run(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the latest run for the current user, if any."""
    filters = [ProcessingRun.status.in_(["completed", "running"])]
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
    """Get processing status and progress."""
    result = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")

    return {
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
