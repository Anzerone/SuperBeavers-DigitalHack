"""Dashboard data endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, and_
from typing import Optional

from backend.storage.database import get_db
from backend.storage.models import Appeal, ProcessingRun, ProblemCluster, Summary

router = APIRouter()


@router.get("/dashboard/stats/{run_id}")
async def get_stats(run_id: int, db: AsyncSession = Depends(get_db)):
    """Get summary statistics for a run."""
    run_q = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = run_q.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")

    filtered = run.total_records or 0
    dropped = (run.dropped_incident_type or 0) + (run.dropped_outcome or 0)
    raw = max(run.raw_records or 0, filtered + dropped)
    if raw <= 0:
        raw = filtered
    filter_drop = max(raw - filtered, 0)
    return {
        "raw_records": raw,
        "total_records": filtered,
        "filter_drop": filter_drop,
        "filter_drop_percent": round(filter_drop / max(raw, 1) * 100, 1),
        "dropped_incident_type": run.dropped_incident_type or 0,
        "dropped_outcome": run.dropped_outcome or 0,
        "problem_count": run.problem_count,
        "problem_percent": round(run.problem_count / max(raw, 1) * 100, 1),
        "cluster_count": run.cluster_count,
    }


@router.get("/dashboard/top/{run_id}")
async def get_top_districts(
    run_id: int,
    n: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Get top-N districts by problem count."""
    result = await db.execute(
        select(
            Appeal.municipality,
            func.count().label("count"),
        )
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(Appeal.municipality)
        .order_by(func.count().desc())
        .limit(n)
    )
    rows = result.all()
    return [{"municipality": r.municipality, "count": r.count} for r in rows]


@router.get("/dashboard/charts/{run_id}")
async def get_chart_data(
    run_id: int,
    municipality: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get chart data with optional filters."""
    base_filter = [Appeal.run_id == run_id, Appeal.is_problem == True]
    if municipality:
        base_filter.append(Appeal.municipality == municipality)
    if severity:
        base_filter.append(Appeal.severity == severity)
    if category:
        base_filter.append(Appeal.category == category)

    # Severity distribution
    sev_q = await db.execute(
        select(Appeal.severity, func.count().label("count"))
        .where(and_(*base_filter))
        .group_by(Appeal.severity)
    )
    severity_data = [{"severity": r.severity, "count": r.count} for r in sev_q.all()]

    # Category distribution
    cat_q = await db.execute(
        select(Appeal.category, func.count().label("count"))
        .where(and_(*base_filter))
        .group_by(Appeal.category)
        .order_by(func.count().desc())
    )
    category_data = [{"category": r.category, "count": r.count} for r in cat_q.all()]

    # Top districts
    dist_q = await db.execute(
        select(Appeal.municipality, func.count().label("count"))
        .where(and_(*base_filter))
        .group_by(Appeal.municipality)
        .order_by(func.count().desc())
        .limit(10)
    )
    district_data = [{"municipality": r.municipality, "count": r.count} for r in dist_q.all()]

    return {
        "severity": severity_data,
        "categories": category_data,
        "districts": district_data,
    }
