"""Dashboard data endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Optional

from backend.api.display_fields import DEFAULT_CATEGORY, appeal_display_category, appeal_display_severity
from backend.storage.database import get_db
from backend.storage.models import Appeal, AppealClusterMap, ProcessingRun, ProblemCluster

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
    display_severity = appeal_display_severity()
    sev_q = await db.execute(
        select(display_severity.label("severity"), func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(display_severity)
    )
    severity_breakdown = {
        (row.severity or "UNKNOWN"): row.count for row in sev_q.all()
    }
    critical_count = severity_breakdown.get("CRITICAL", 0)
    high_count = severity_breakdown.get("HIGH", 0)
    severe_count = critical_count + high_count

    # Top-3 categories with counts
    display_category = appeal_display_category()
    top_cat_q = await db.execute(
        select(display_category.label("category"), func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(display_category)
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
        select(func.count(func.distinct(display_category)))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
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

    prev_q = await db.execute(
        select(ProcessingRun)
        .where(
            and_(
                ProcessingRun.user_id == run.user_id,
                ProcessingRun.id < run_id,
                ProcessingRun.status == "completed",
            )
        )
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )
    previous_run = prev_q.scalar_one_or_none()
    previous_problem_count = previous_run.problem_count if previous_run else None
    current_problem_count = run.problem_count or 0
    problem_delta = None
    problem_delta_percent = None
    problem_growth_direction = "none"
    if previous_problem_count is not None:
        previous_problem_count = previous_problem_count or 0
        problem_delta = current_problem_count - previous_problem_count
        if previous_problem_count > 0:
            problem_delta_percent = round(problem_delta / previous_problem_count * 100, 1)
        if problem_delta > 0:
            problem_growth_direction = "up"
        elif problem_delta < 0:
            problem_growth_direction = "down"
        else:
            problem_growth_direction = "flat"

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
        "problem_growth": {
            "has_previous": previous_run is not None,
            "previous_run_id": previous_run.id if previous_run else None,
            "previous_problem_count": previous_problem_count,
            "delta": problem_delta,
            "delta_percent": problem_delta_percent,
            "direction": problem_growth_direction,
        },
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
    granularity: str = Query("day", pattern="^(day|week|month)$"),
    scope: str = Query("appeals", pattern="^(appeals|clusters)$"),
    db: AsyncSession = Depends(get_db),
):
    """Динамика обращений во времени, опц. с разбивкой по категории."""
    trunc_unit = {"day": "day", "week": "week", "month": "month"}[granularity]
    bucket = func.date_trunc(trunc_unit, Appeal.date_created).label("bucket")
    count_expr = func.count(func.distinct(Appeal.id)).label("count")

    if scope == "clusters":
        cluster_category = func.coalesce(func.nullif(ProblemCluster.category, ""), DEFAULT_CATEGORY)
        from_obj = (
            Appeal.__table__
            .join(AppealClusterMap.__table__, AppealClusterMap.appeal_id == Appeal.id)
            .join(ProblemCluster.__table__, ProblemCluster.id == AppealClusterMap.cluster_id)
        )
        category_col = cluster_category
        base = [
            Appeal.run_id == run_id,
            Appeal.is_problem == True,
            Appeal.date_created.isnot(None),
            ProblemCluster.run_id == run_id,
        ]
        for clause in (
            _multi(ProblemCluster.municipality, municipality),
            _multi(cluster_category, category),
            _multi(ProblemCluster.severity, severity),
        ):
            if clause is not None:
                base.append(clause)
    else:
        from_obj = Appeal.__table__
        display_severity = appeal_display_severity()
        display_category = appeal_display_category()
        base = [Appeal.run_id == run_id, Appeal.is_problem == True, Appeal.date_created.isnot(None)]
        for clause in (
            _multi(Appeal.municipality, municipality),
            _multi(display_category, category),
            _multi(display_severity, severity),
        ):
            if clause is not None:
                base.append(clause)
        category_col = display_category

    # Total per bucket
    total_q = await db.execute(
        select(bucket, count_expr)
        .select_from(from_obj)
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
            select(category_col, count_expr)
            .select_from(from_obj)
            .where(and_(*base, category_col.isnot(None)))
            .group_by(category_col)
            .order_by(count_expr.desc())
            .limit(5)
        )
        top_cat_names = [r[0] for r in top_cats_q.all()]
    else:
        top_cat_names = _split_multi(category)

    by_category: dict[str, dict[str, int]] = {}
    if top_cat_names:
        cat_trend_q = await db.execute(
            select(bucket, category_col, count_expr)
            .select_from(from_obj)
            .where(and_(*base, category_col.in_(top_cat_names)))
            .group_by(bucket, category_col)
            .order_by(bucket)
        )
        for row in cat_trend_q.all():
            if not row.bucket:
                continue
            key = row.bucket.isoformat()
            by_category.setdefault(key, {})[row[1]] = row.count

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
    scope: str = Query("clusters", pattern="^(appeals|clusters)$"),
    db: AsyncSession = Depends(get_db),
):
    """Get chart data with optional filters."""
    display_severity = appeal_display_severity()
    display_category = appeal_display_category()
    appeal_filter = [Appeal.run_id == run_id, Appeal.is_problem == True]
    for clause in (
        _multi(Appeal.municipality, municipality),
        _multi(display_severity, severity),
        _multi(display_category, category),
    ):
        if clause is not None:
            appeal_filter.append(clause)

    if scope == "clusters":
        cluster_category = func.coalesce(func.nullif(ProblemCluster.category, ""), DEFAULT_CATEGORY)
        chart_from = (
            Appeal.__table__
            .join(AppealClusterMap.__table__, AppealClusterMap.appeal_id == Appeal.id)
            .join(ProblemCluster.__table__, ProblemCluster.id == AppealClusterMap.cluster_id)
        )
        chart_filter = [
            Appeal.run_id == run_id,
            Appeal.is_problem == True,
            ProblemCluster.run_id == run_id,
        ]
        for clause in (
            _multi(ProblemCluster.municipality, municipality),
            _multi(ProblemCluster.severity, severity),
            _multi(cluster_category, category),
        ):
            if clause is not None:
                chart_filter.append(clause)
        chart_municipality = ProblemCluster.municipality
        chart_category = cluster_category
    else:
        chart_from = Appeal.__table__
        chart_filter = appeal_filter
        chart_municipality = Appeal.municipality
        chart_category = display_category

    # Severity distribution — считается по кластерам проблем (а не по обращениям).
    # Так donut показывает «сколько уникальных проблем какой тяжести», что
    # релевантнее для руководства: один большой кластер ≠ много мелких,
    # но в обращениях он бы «съедал» весь график.
    cluster_filter = [ProblemCluster.run_id == run_id]
    if municipality:
        cluster_filter.append(_multi(ProblemCluster.municipality, municipality))
    if severity:
        cluster_filter.append(_multi(ProblemCluster.severity, severity))
    if category:
        cluster_category = func.coalesce(func.nullif(ProblemCluster.category, ""), DEFAULT_CATEGORY)
        cluster_filter.append(_multi(cluster_category, category))
    cluster_filter = [c for c in cluster_filter if c is not None]

    sev_q = await db.execute(
        select(ProblemCluster.severity, func.count().label("count"))
        .where(and_(*cluster_filter))
        .group_by(ProblemCluster.severity)
    )
    severity_data = [{"severity": r.severity, "count": r.count} for r in sev_q.all()]

    # Также считаем severity по обращениям (для вкладки «Обращения»).
    sev_appeals_q = await db.execute(
        select(display_severity.label("severity"), func.count().label("count"))
        .where(and_(*appeal_filter))
        .group_by(display_severity)
    )
    severity_appeals_data = [{"severity": r.severity, "count": r.count} for r in sev_appeals_q.all()]

    # Category distribution
    cat_q = await db.execute(
        select(chart_category, func.count(func.distinct(Appeal.id)).label("count"))
        .select_from(chart_from)
        .where(and_(*chart_filter))
        .group_by(chart_category)
        .order_by(func.count(func.distinct(Appeal.id)).desc())
    )
    category_data = [{"category": r[0], "count": r.count} for r in cat_q.all()]

    # All districts (карта показывает все, графики берут топ-10 на фронте)
    dist_q = await db.execute(
        select(chart_municipality, func.count(func.distinct(Appeal.id)).label("count"))
        .select_from(chart_from)
        .where(and_(*chart_filter))
        .group_by(chart_municipality)
        .order_by(func.count(func.distinct(Appeal.id)).desc())
        .limit(50)
    )
    district_data = [{"municipality": r[0], "count": r.count} for r in dist_q.all()]

    return {
        "severity": severity_data,                    # по кластерам (для вкладки «Кластеры»)
        "severity_appeals": severity_appeals_data,    # по обращениям (для вкладки «Обращения»)
        "categories": category_data,
        "districts": district_data,
    }
