"""Processing endpoints: start pipeline and check status."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.storage.database import get_db
from backend.storage.models import ProcessingRun

router = APIRouter()


@router.post("/process")
async def start_processing(
    request: Request,
    filepath: str,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Start background processing pipeline."""
    user_id = request.app.state.default_user_id

    run = ProcessingRun(
        user_id=user_id,
        filename=filename,
        status="running",
        current_step="Загрузка файла",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    asyncio.create_task(_run_pipeline(run.id, filepath))

    return {"run_id": run.id, "status": "running"}


async def _run_pipeline(run_id: int, filepath: str):
    """Run the full processing pipeline in a background thread."""
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


@router.get("/processing/{run_id}/status")
async def get_status(run_id: int, db: AsyncSession = Depends(get_db)):
    """Get processing status and progress."""
    result = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")

    return {
        "run_id": run.id,
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
