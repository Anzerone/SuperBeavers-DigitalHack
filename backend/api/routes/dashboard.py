"""Dashboard data endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Optional

from backend.storage.database import get_db
from backend.storage.models import Appeal, ProcessingRun, ProblemCluster

router = APIRouter()


# Разделитель мультивыбора — символ Unit Separator (\x1f), которого нет
# в реальных значениях (запятая встречается, напр. "Омская область, другое").
FILTER_SEP = "\x1f"


def _split_multi(value):
    if not value:
        return []
    return [p.strip() for p in str(value).split(FILTER_SEP) if p.strip()]


def _multi(column, value):
    """Построить условие для фильтра, поддерживая мультивыбор."""
    parts = _split_multi(value)
    if not parts:
        return None
    if len(parts) == 1:
        return column == parts[0]
    return column.in_(parts)


@router.get("/dashboard/stats/{run_id}")
async def get_stats(run_id: int, db: AsyncSession = Depends(get_db)):
    """Rich summary statistics for a run.

    Includes raw KPIs (raw_records, problem_count, ...) and pre-computed
    breakdowns the dashboard needs to render rich, contextual cards without
    multiple extra requests.
    """
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

    # Severity breakdown of problems
    sev_q = await db.execute(
        select(Appeal.severity, func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(Appeal.severity)
    )
    severity_breakdown = {
        (row.severity or "UNKNOWN"): row.count for row in sev_q.all()
    }
    critical_count = severity_breakdown.get("CRITICAL", 0)
    high_count = severity_breakdown.get("HIGH", 0)
    severe_count = critical_count + high_count

    # Top-3 categories with counts
    top_cat_q = await db.execute(
        select(Appeal.category, func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True, Appeal.category.isnot(None)))
        .group_by(Appeal.category)
        .order_by(func.count().desc())
        .limit(3)
    )
    top_categories = [{"name": r.category, "count": r.count} for r in top_cat_q.all()]

    # Top-3 municipalities
    top_muni_q = await db.execute(
        select(Appeal.municipality, func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(Appeal.municipality)
        .order_by(func.count().desc())
        .limit(3)
    )
    top_municipalities = [{"name": r.municipality, "count": r.count} for r in top_muni_q.all()]

    # Number of distinct municipalities & categories with problems
    muni_count_q = await db.execute(
        select(func.count(func.distinct(Appeal.municipality)))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
    )
    muni_count = muni_count_q.scalar() or 0

    cat_count_q = await db.execute(
        select(func.count(func.distinct(Appeal.category)))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True, Appeal.category.isnot(None)))
    )
    distinct_categories = cat_count_q.scalar() or 0

    # Average problems per municipality
    avg_per_muni = round((run.problem_count or 0) / max(muni_count, 1), 1)

    # Sentiment breakdown
    sent_q = await db.execute(
        select(Appeal.sentiment, func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True, Appeal.sentiment.isnot(None)))
        .group_by(Appeal.sentiment)
    )
    sentiment_breakdown = {row.sentiment: row.count for row in sent_q.all()}

    # Average cluster size
    cluster_size_q = await db.execute(
        select(func.avg(ProblemCluster.appeal_count))
        .where(ProblemCluster.run_id == run_id)
    )
    avg_cluster_size = round(float(cluster_size_q.scalar() or 0), 1)

    # Largest cluster
    largest_q = await db.execute(
        select(ProblemCluster.cluster_name, ProblemCluster.municipality, ProblemCluster.appeal_count)
        .where(ProblemCluster.run_id == run_id)
        .order_by(ProblemCluster.appeal_count.desc())
        .limit(1)
    )
    largest_row = largest_q.first()
    largest_cluster = (
        {
            "name": largest_row.cluster_name,
            "municipality": largest_row.municipality,
            "count": largest_row.appeal_count,
        }
        if largest_row
        else None
    )

    return {
        "raw_records": raw,
        "total_records": filtered,
        "filter_drop": filter_drop,
        "filter_drop_percent": round(filter_drop / max(raw, 1) * 100, 1),
        "dropped_incident_type": run.dropped_incident_type or 0,
        "dropped_outcome": run.dropped_outcome or 0,
        "problem_count": run.problem_count,
        "problem_percent": round((run.problem_count or 0) / max(filtered, 1) * 100, 1),
        "cluster_count": run.cluster_count,
        "critical_count": critical_count,
        "high_count": high_count,
        "severe_count": severe_count,
        "severe_percent": round(severe_count / max(run.problem_count or 0, 1) * 100, 1),
        "severity_breakdown": severity_breakdown,
        "top_categories": top_categories,
        "top_municipalities": top_municipalities,
        "municipality_count": muni_count,
        "distinct_categories": distinct_categories,
        "avg_per_municipality": avg_per_muni,
        "avg_cluster_size": avg_cluster_size,
        "largest_cluster": largest_cluster,
        "sentiment_breakdown": sentiment_breakdown,
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


@router.get("/dashboard/timeline/{run_id}")
async def get_timeline(
    run_id: int,
    municipality: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    granularity: str = Query("day", regex="^(day|week|month)$"),
    db: AsyncSession = Depends(get_db),
):
    """Динамика обращений во времени, опц. с разбивкой по категории."""
    base = [Appeal.run_id == run_id, Appeal.is_problem == True, Appeal.date_created.isnot(None)]
    for clause in (_multi(Appeal.municipality, municipality), _multi(Appeal.category, category), _multi(Appeal.severity, severity)):
        if clause is not None:
            base.append(clause)

    trunc_unit = {"day": "day", "week": "week", "month": "month"}[granularity]
    bucket = func.date_trunc(trunc_unit, Appeal.date_created).label("bucket")

    # Total per bucket
    total_q = await db.execute(
        select(bucket, func.count().label("count"))
        .where(and_(*base))
        .group_by(bucket)
        .order_by(bucket)
    )
    timeline = [
        {"date": r.bucket.isoformat() if r.bucket else None, "count": r.count}
        for r in total_q.all() if r.bucket
    ]

    # Category trends. When category filters are active, keep the selected
    # categories as separate series instead of returning only the total line.
    if not category:
        top_cats_q = await db.execute(
            select(Appeal.category, func.count().label("count"))
            .where(and_(*base, Appeal.category.isnot(None)))
            .group_by(Appeal.category)
            .order_by(func.count().desc())
            .limit(5)
        )
        top_cat_names = [r.category for r in top_cats_q.all()]
    else:
        top_cat_names = _split_multi(category)

    by_category: dict[str, dict[str, int]] = {}
    if top_cat_names:
        cat_trend_q = await db.execute(
            select(bucket, Appeal.category, func.count().label("count"))
            .where(and_(*base, Appeal.category.in_(top_cat_names)))
            .group_by(bucket, Appeal.category)
            .order_by(bucket)
        )
        for row in cat_trend_q.all():
            if not row.bucket:
                continue
            key = row.bucket.isoformat()
            by_category.setdefault(key, {})[row.category] = row.count

    return {
        "granularity": granularity,
        "timeline": timeline,
        "top_categories": top_cat_names,
        "by_category": by_category,
    }


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
    for clause in (_multi(Appeal.municipality, municipality), _multi(Appeal.severity, severity), _multi(Appeal.category, category)):
        if clause is not None:
            base_filter.append(clause)

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

    # All districts (карта показывает все, графики берут топ-10 на фронте)
    dist_q = await db.execute(
        select(Appeal.municipality, func.count().label("count"))
        .where(and_(*base_filter))
        .group_by(Appeal.municipality)
        .order_by(func.count().desc())
        .limit(50)
    )
    district_data = [{"municipality": r.municipality, "count": r.count} for r in dist_q.all()]

    return {
        "severity": severity_data,
        "categories": category_data,
        "districts": district_data,
    }
