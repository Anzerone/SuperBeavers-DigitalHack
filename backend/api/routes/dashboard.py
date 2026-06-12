"""Dashboard data endpoints."""
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import Optional

from backend.config import EXCLUDE_OUTCOMES
from backend.api.display_fields import DEFAULT_CATEGORY, appeal_display_category, appeal_display_severity
from backend.storage.database import get_db
from backend.storage.models import Appeal, AppealClusterMap, ProcessingRun, ProblemCluster, RunLoadStats

router = APIRouter()


# Разделитель мультивыбора — символ Unit Separator (\x1f), которого нет
# в реальных значениях (запятая встречается, напр. "Омская область, другое").
FILTER_SEP = "\x1f"
CLOSED_OUTCOME_VALUES = tuple(EXCLUDE_OUTCOMES)
OPEN_OUTCOME_VALUES = ("Открыто", "открыто", "ОТКРЫТО")
DEFERRED_OUTCOME_VALUES = ("Отложено", "отложено", "ОТЛОЖЕНО")
NOT_RESOLVED_OUTCOME_VALUES = ("Не решено", "не решено", "НЕ РЕШЕНО")


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


def _norm_cluster_name(value: str | None) -> str:
    text_value = " ".join(str(value or "").casefold().replace("ё", "е").split())
    return text_value[:90]


# Служебные слова не несут смысла проблемы — без них сходство имен честнее.
_NAME_STOPWORDS = {
    "и", "в", "на", "с", "по", "за", "из", "у", "о", "об", "для", "до", "от",
    "не", "нет", "при", "под", "над", "это",
}


def _name_tokens(value: str | None) -> set[str]:
    """Слова-основы имени кластера: первые 6 букв слова грубо снимают окончания."""
    tokens = set()
    for token in _norm_cluster_name(value).split():
        if len(token) < 3 or token in _NAME_STOPWORDS:
            continue
        tokens.add(token[:6])
    return tokens


def _cluster_name_similarity(a: str | None, b: str | None) -> float:
    """0..1: насколько похожи имена кластеров (одинаковый смысл ≠ одинаковая формулировка)."""
    na = _norm_cluster_name(a)
    nb = _norm_cluster_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    short = min(len(na), len(nb), 42)
    if short >= 16 and na[:short] == nb[:short]:
        return 0.95
    ta = _name_tokens(a)
    tb = _name_tokens(b)
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    if not intersection:
        return 0.0
    jaccard = intersection / len(ta | tb)
    overlap = intersection / min(len(ta), len(tb))
    # Jaccard ловит «почти одинаковые» имена, overlap — когда одно имя
    # вложено в другое с дополнительными словами.
    return max(jaccard, overlap if intersection >= 2 else 0.0)


_CLUSTER_MATCH_THRESHOLD = 0.5


async def _previous_completed_run(db: AsyncSession, run: ProcessingRun) -> ProcessingRun | None:
    prev_q = await db.execute(
        select(ProcessingRun)
        .where(
            and_(
                ProcessingRun.user_id == run.user_id,
                ProcessingRun.id < run.id,
                ProcessingRun.status == "completed",
            )
        )
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )
    return prev_q.scalar_one_or_none()


async def _cluster_comparison(db: AsyncSession, run: ProcessingRun, previous_run: ProcessingRun | None) -> dict:
    cur_q = await db.execute(
        select(ProblemCluster)
        .where(ProblemCluster.run_id == run.id)
        .order_by(ProblemCluster.appeal_count.desc())
    )
    current_clusters = cur_q.scalars().all()
    if not previous_run:
        return {
            "has_previous": False,
            "previous_run_id": None,
            "new_clusters": len(current_clusters),
            "resolved_clusters": 0,
            "continuing_clusters": 0,
            "growing_clusters": 0,
            "shrinking_clusters": 0,
            "stable_clusters": 0,
            "new_appeals": sum(c.appeal_count or 0 for c in current_clusters),
            "resolved_appeals": 0,
            "continuing_appeals": 0,
            "top_new": [
                {
                    "cluster_id": c.id,
                    "name": c.cluster_name,
                    "municipality": c.municipality,
                    "category": c.category,
                    "appeal_count": c.appeal_count or 0,
                    "severity": c.severity,
                }
                for c in current_clusters[:10]
            ],
            "top_resolved": [],
            "top_growing": [],
            "top_shrinking": [],
        }

    prev_q = await db.execute(select(ProblemCluster).where(ProblemCluster.run_id == previous_run.id))
    previous_clusters = prev_q.scalars().all()
    prev_by_scope: dict[tuple[str | None, str | None], list[ProblemCluster]] = defaultdict(list)
    for cluster in previous_clusters:
        prev_by_scope[(cluster.municipality, cluster.category)].append(cluster)

    matched_prev_ids: set[int] = set()
    new_clusters = []
    growing = []
    shrinking = []
    stable = 0
    continuing_appeals = 0
    for current in current_clusters:
        candidates = prev_by_scope.get((current.municipality, current.category), [])
        # Выбираем лучшего по сходству, а не первого подходящего: имена кластеров
        # между прогонами различаются формулировками при одинаковом смысле.
        match = None
        best_score = 0.0
        for previous in candidates:
            if previous.id in matched_prev_ids:
                continue
            score = _cluster_name_similarity(current.cluster_name, previous.cluster_name)
            if score > best_score:
                best_score = score
                match = previous
        if not match or best_score < _CLUSTER_MATCH_THRESHOLD:
            new_clusters.append(current)
            continue

        matched_prev_ids.add(match.id)
        current_count = current.appeal_count or 0
        previous_count = match.appeal_count or 0
        continuing_appeals += current_count
        delta = current_count - previous_count
        item = {
            "cluster_id": current.id,
            "name": current.cluster_name,
            "municipality": current.municipality,
            "category": current.category,
            "appeal_count": current_count,
            "previous_count": previous_count,
            "delta": delta,
            "severity": current.severity,
        }
        if delta > 0:
            growing.append(item)
        elif delta < 0:
            shrinking.append(item)
        else:
            stable += 1

    resolved_clusters = [cluster for cluster in previous_clusters if cluster.id not in matched_prev_ids]
    growing.sort(key=lambda item: item["delta"], reverse=True)
    shrinking.sort(key=lambda item: item["delta"])
    resolved_clusters.sort(key=lambda c: c.appeal_count or 0, reverse=True)

    return {
        "has_previous": True,
        "previous_run_id": previous_run.id,
        "new_clusters": len(new_clusters),
        "resolved_clusters": len(resolved_clusters),
        "continuing_clusters": len(current_clusters) - len(new_clusters),
        "growing_clusters": len(growing),
        "shrinking_clusters": len(shrinking),
        "stable_clusters": stable,
        "new_appeals": sum(c.appeal_count or 0 for c in new_clusters),
        "resolved_appeals": sum(c.appeal_count or 0 for c in resolved_clusters),
        "continuing_appeals": continuing_appeals,
        "top_new": [
            {
                "cluster_id": c.id,
                "name": c.cluster_name,
                "municipality": c.municipality,
                "category": c.category,
                "appeal_count": c.appeal_count or 0,
                "severity": c.severity,
            }
            for c in sorted(new_clusters, key=lambda c: c.appeal_count or 0, reverse=True)[:10]
        ],
        "top_resolved": [
            {
                "cluster_id": c.id,
                "name": c.cluster_name,
                "municipality": c.municipality,
                "category": c.category,
                "previous_count": c.appeal_count or 0,
                "severity": c.severity,
            }
            for c in resolved_clusters[:10]
        ],
        "top_growing": growing[:10],
        "top_shrinking": shrinking[:10],
    }


async def _counts_by_dimension(db: AsyncSession, run_id: int, dimension: str) -> dict[str, int]:
    display_category = appeal_display_category()
    column = Appeal.municipality if dimension == "municipality" else display_category
    result = await db.execute(
        select(column.label("name"), func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(column)
    )
    return {row.name or "Не указано": row.count for row in result.all()}


def _dimension_delta(current: dict[str, int], previous: dict[str, int], limit: int = 10) -> list[dict]:
    rows = []
    for name in set(current) | set(previous):
        current_count = current.get(name, 0)
        previous_count = previous.get(name, 0)
        delta = current_count - previous_count
        rows.append({
            "name": name,
            "count": current_count,
            "previous_count": previous_count,
            "delta": delta,
            "delta_percent": round(delta / previous_count * 100, 1) if previous_count else None,
        })
    rows.sort(key=lambda item: abs(item["delta"]), reverse=True)
    return rows[:limit]


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

    previous_run = await _previous_completed_run(db, run)
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
    change_breakdown = await _cluster_comparison(db, run, previous_run)

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
            "breakdown": change_breakdown,
        },
    }


@router.get("/dashboard/comparison/{run_id}")
async def get_run_comparison(run_id: int, db: AsyncSession = Depends(get_db)):
    run_q = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = run_q.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")

    previous_run = await _previous_completed_run(db, run)
    cluster_change = await _cluster_comparison(db, run, previous_run)
    if not previous_run:
        return {
            "has_previous": False,
            "previous_run_id": None,
            "cluster_change": cluster_change,
            "categories": [],
            "municipalities": [],
        }

    current_categories = await _counts_by_dimension(db, run.id, "category")
    previous_categories = await _counts_by_dimension(db, previous_run.id, "category")
    current_municipalities = await _counts_by_dimension(db, run.id, "municipality")
    previous_municipalities = await _counts_by_dimension(db, previous_run.id, "municipality")

    return {
        "has_previous": True,
        "previous_run_id": previous_run.id,
        "current_problem_count": run.problem_count or 0,
        "previous_problem_count": previous_run.problem_count or 0,
        "delta": (run.problem_count or 0) - (previous_run.problem_count or 0),
        "cluster_change": cluster_change,
        "categories": _dimension_delta(current_categories, previous_categories),
        "municipalities": _dimension_delta(current_municipalities, previous_municipalities),
    }


@router.get("/dashboard/resolved/{run_id}")
async def get_resolved_analytics(run_id: int, db: AsyncSession = Depends(get_db)):
    run_q = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = run_q.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")

    display_category = appeal_display_category()
    # Status is driven by the source "outcome" field. In this register a
    # filled date_closed can appear on rows whose outcome is still empty, so
    # date_closed alone would incorrectly move active rows to "closed".
    run_filter = Appeal.run_id == run_id
    outcome_value = func.nullif(func.trim(Appeal.outcome), "")
    closed_outcome_filter = outcome_value.in_(CLOSED_OUTCOME_VALUES)
    open_outcome_filter = or_(outcome_value.is_(None), outcome_value.in_(OPEN_OUTCOME_VALUES))
    deferred_filter = outcome_value.in_(DEFERRED_OUTCOME_VALUES)
    not_resolved_filter = outcome_value.in_(NOT_RESOLVED_OUTCOME_VALUES)
    active_outcome_filter = or_(open_outcome_filter, deferred_filter, not_resolved_filter)
    resolved_filter = and_(Appeal.run_id == run_id, closed_outcome_filter)
    resolution_days = func.extract("epoch", Appeal.date_closed - Appeal.date_created) / 86400
    # В средний срок берём только корректные интервалы: в данных встречаются
    # строки с датой закрытия раньше даты создания — они давали отрицательный срок.
    valid_resolution = and_(Appeal.date_closed.isnot(None), Appeal.date_closed >= Appeal.date_created)

    totals_q = await db.execute(
        select(
            func.count().label("processed"),
            func.count().filter(and_(Appeal.is_problem == True, active_outcome_filter)).label("problems"),
            func.count().filter(closed_outcome_filter).label("resolved"),
            func.count().filter(active_outcome_filter).label("open"),
            func.count().filter(open_outcome_filter).label("open_by_outcome"),
            func.count().filter(deferred_filter).label("deferred"),
            func.count().filter(not_resolved_filter).label("not_resolved"),
            func.avg(resolution_days)
            .filter(and_(closed_outcome_filter, valid_resolution))
            .label("avg_days"),
            func.sum(resolution_days)
            .filter(and_(closed_outcome_filter, valid_resolution))
            .label("resolution_sum"),
            func.count()
            .filter(and_(closed_outcome_filter, valid_resolution))
            .label("resolution_n"),
        )
        .where(run_filter)
    )
    totals = totals_q.one()
    processed_count = totals.processed or 0
    problem_count = totals.problems or 0
    resolved_in_data = totals.resolved or 0
    open_count = totals.open or 0
    open_by_outcome_count = totals.open_by_outcome or 0
    deferred_count = totals.deferred or 0
    not_resolved_count = totals.not_resolved or 0
    in_data_resolution_sum = float(totals.resolution_sum or 0)
    in_data_resolution_n = int(totals.resolution_n or 0)

    # Большая часть решённых обращений отфильтровывается ещё при загрузке файла
    # (итоги «Закрыто», «Разъяснено», «Решено», «Перенаправлено») — учитываем их,
    # чтобы «Решено» считалось от всех записей исходного файла. «Нерешаемые»
    # (тип инцидента «Нерешаемый») показываем отдельным блоком.
    resolved_prefiltered = run.dropped_outcome or 0
    unsolvable_count = run.dropped_incident_type or 0
    resolved_count = resolved_in_data + resolved_prefiltered
    raw_total = max(run.raw_records or 0, processed_count + resolved_prefiltered + unsolvable_count)
    dropped_unanalyzed_count = max(raw_total - processed_count - resolved_prefiltered - unsolvable_count, 0)

    # Разрезы отброшенных при загрузке строк (закрытые до анализа): без них
    # графики по районам/категориям/месяцам показывали только малую часть
    # решённых — ту, что попала в анализируемую выборку.
    load_stats_q = await db.execute(select(RunLoadStats).where(RunLoadStats.run_id == run_id))
    load_stats_row = load_stats_q.scalar_one_or_none()
    prefiltered = (load_stats_row.prefiltered if load_stats_row else None) or {}
    pre_resolved = prefiltered.get("resolved") or {}
    pre_by_municipality = pre_resolved.get("by_municipality") or {}
    pre_by_category = pre_resolved.get("by_category") or {}
    pre_by_outcome = pre_resolved.get("by_outcome") or {}
    pre_timeline = pre_resolved.get("timeline") or {}

    # Средний срок решения считаем по всем решённым обращениям с корректными
    # датами: и по выборке (appeals), и по «закрытым до анализа» строкам, чей
    # агрегат (сумма дней + количество) сохранён при загрузке файла.
    pre_resolution = pre_resolved.get("resolution") or {}
    total_resolution_sum = in_data_resolution_sum + float(pre_resolution.get("sum_days") or 0)
    total_resolution_n = in_data_resolution_n + int(pre_resolution.get("count") or 0)
    avg_days = round(total_resolution_sum / total_resolution_n, 1) if total_resolution_n else 0.0

    def _merge_breakdown(db_rows, key_name, pre_counts, limit):
        """Combine DB-level resolved/open counts with prefiltered-resolved counts."""
        merged: dict[str, dict] = {}
        for row in db_rows:
            key = getattr(row, key_name) or "Не указано"
            merged[key] = {
                key_name: key,
                "resolved": row.resolved or 0,
                "open": row.open or 0,
                "total": row.total or 0,
                "avg_days": round(float(row.avg_days or 0), 1),
            }
        for key, count in pre_counts.items():
            key = str(key).strip() or "Не указано"
            entry = merged.setdefault(
                key,
                {key_name: key, "resolved": 0, "open": 0, "total": 0, "avg_days": 0.0},
            )
            entry["resolved"] += int(count)
            entry["total"] += int(count)
        items = sorted(merged.values(), key=lambda item: item["total"], reverse=True)[:limit]
        for entry in items:
            entry["rate"] = round(entry["resolved"] / max(entry["total"], 1) * 100, 1)
        return items

    by_category_q = await db.execute(
        select(
            display_category.label("category"),
            func.count().filter(closed_outcome_filter).label("resolved"),
            func.count().filter(active_outcome_filter).label("open"),
            func.count().label("total"),
            func.avg(resolution_days).filter(and_(closed_outcome_filter, valid_resolution)).label("avg_days"),
        )
        .where(run_filter)
        .group_by(display_category)
    )
    by_category = _merge_breakdown(by_category_q.all(), "category", pre_by_category, 12)

    by_muni_q = await db.execute(
        select(
            Appeal.municipality,
            func.count().filter(closed_outcome_filter).label("resolved"),
            func.count().filter(active_outcome_filter).label("open"),
            func.count().label("total"),
            func.avg(resolution_days).filter(and_(closed_outcome_filter, valid_resolution)).label("avg_days"),
        )
        .where(run_filter)
        .group_by(Appeal.municipality)
    )
    by_municipality = _merge_breakdown(by_muni_q.all(), "municipality", pre_by_municipality, 15)

    other_active_count = max(open_count - open_by_outcome_count - deferred_count - not_resolved_count, 0)
    pre_analysis_summary = [
        {
            "status": "Уже завершены до ML-анализа",
            "detail": "Итог: Решено / Закрыто / Разъяснено / Перенаправлено",
            "count": resolved_prefiltered,
            "tone": "closed",
        },
        {
            "status": "Нерешаемые до ML-анализа",
            "detail": "Тип инцидента: Нерешаемый",
            "count": unsolvable_count,
            "tone": "warning",
        },
        {
            "status": "Не попали в анализ",
            "detail": "Пустой или слишком короткий текст обращения",
            "count": dropped_unanalyzed_count,
            "tone": "skipped",
        },
        {
            "status": "Переданы в ML-анализ",
            "detail": "По этим строкам строятся проблемность, категории и тяжесть",
            "count": processed_count,
            "tone": "processed",
        },
    ]
    pre_analysis_summary = [item for item in pre_analysis_summary if item["count"] > 0]

    post_analysis_summary = [
        {"status": "Открыто или итог пустой", "count": open_by_outcome_count, "tone": "open"},
        {"status": "Отложено", "count": deferred_count, "tone": "open"},
        {"status": "Итог «Не решено»", "count": not_resolved_count, "tone": "warning"},
        {"status": "Другой незакрытый итог", "count": other_active_count, "tone": "open"},
    ]
    post_analysis_summary = [item for item in post_analysis_summary if item["count"] > 0]
    archived_analysis_summary = [
        {"status": "Закрытые итоги внутри анализа", "count": resolved_in_data, "tone": "closed"},
    ]
    archived_analysis_summary = [item for item in archived_analysis_summary if item["count"] > 0]

    outcome_q = await db.execute(
        select(Appeal.outcome, func.count().label("count"))
        .where(resolved_filter)
        .group_by(Appeal.outcome)
    )
    outcome_counts: dict[str, int] = {}
    for row in outcome_q.all():
        key = (row.outcome or "Не указано").strip() or "Не указано"
        outcome_counts[key] = outcome_counts.get(key, 0) + int(row.count or 0)
    for key, count in pre_by_outcome.items():
        key = str(key).strip() or "Не указано"
        outcome_counts[key] = outcome_counts.get(key, 0) + int(count)
    outcomes = [
        {"outcome": key, "count": count}
        for key, count in sorted(outcome_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    ]

    timeline_q = await db.execute(
        select(func.date_trunc("month", Appeal.date_closed).label("bucket"), func.count().label("count"))
        .where(resolved_filter)
        .group_by("bucket")
        .order_by("bucket")
    )
    timeline_counts: dict[str, int] = {}
    for row in timeline_q.all():
        if not row.bucket:
            continue
        timeline_counts[row.bucket.isoformat()] = timeline_counts.get(row.bucket.isoformat(), 0) + int(row.count or 0)
    for bucket, count in pre_timeline.items():
        key = str(bucket)
        timeline_counts[key] = timeline_counts.get(key, 0) + int(count)
    timeline = [
        {"date": bucket, "count": count}
        for bucket, count in sorted(timeline_counts.items())
    ]

    return {
        "raw_records": raw_total,
        "processed_records": processed_count,
        "active_records": open_count,
        "active_problem_count": problem_count,
        "problem_count": problem_count,
        "non_problem_count": max(open_count - problem_count, 0),
        "resolved_count": resolved_count,
        "resolved_in_data": resolved_in_data,
        "resolved_prefiltered": resolved_prefiltered,
        "unsolvable_count": unsolvable_count,
        "dropped_unanalyzed_count": dropped_unanalyzed_count,
        "open_count": open_count,
        "open_by_outcome_count": open_by_outcome_count,
        "deferred_count": deferred_count,
        "not_resolved_count": not_resolved_count,
        "resolution_rate": round(resolved_count / max(raw_total, 1) * 100, 1),
        "avg_resolution_days": avg_days,
        "by_category": by_category,
        "by_municipality": by_municipality,
        "outcomes": outcomes,
        "pre_analysis_summary": pre_analysis_summary,
        "post_analysis_summary": post_analysis_summary,
        "archived_analysis_summary": archived_analysis_summary,
        "closure_summary": post_analysis_summary,
        "timeline": timeline,
    }


@router.get("/dashboard/report-insights/{run_id}")
async def get_report_insights(run_id: int, db: AsyncSession = Depends(get_db)):
    run_q = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = run_q.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")

    display_category = appeal_display_category()
    display_severity = appeal_display_severity()

    category_q = await db.execute(
        select(display_category.label("category"), func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(display_category)
        .order_by(func.count().desc())
        .limit(8)
    )
    top_categories = [{"category": row.category, "count": row.count} for row in category_q.all()]
    top_category_names = [row["category"] for row in top_categories]

    muni_q = await db.execute(
        select(Appeal.municipality, func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(Appeal.municipality)
        .order_by(func.count().desc())
        .limit(8)
    )
    top_municipalities = [{"municipality": row.municipality, "count": row.count} for row in muni_q.all()]
    top_muni_names = [row["municipality"] for row in top_municipalities]

    matrix = []
    if top_category_names and top_muni_names:
        matrix_q = await db.execute(
            select(Appeal.municipality, display_category.label("category"), func.count().label("count"))
            .where(
                and_(
                    Appeal.run_id == run_id,
                    Appeal.is_problem == True,
                    Appeal.municipality.in_(top_muni_names),
                    display_category.in_(top_category_names),
                )
            )
            .group_by(Appeal.municipality, display_category)
        )
        matrix = [
            {"municipality": row.municipality, "category": row.category, "count": row.count}
            for row in matrix_q.all()
        ]

    severity_by_category_q = await db.execute(
        select(display_category.label("category"), display_severity.label("severity"), func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(display_category, display_severity)
        .order_by(display_category, display_severity)
    )
    severity_by_category = [
        {"category": row.category, "severity": row.severity, "count": row.count}
        for row in severity_by_category_q.all()
    ]

    long_open_q = await db.execute(
        select(
            Appeal.municipality,
            func.count().label("count"),
            func.avg(func.extract("epoch", func.now() - Appeal.date_created) / 86400).label("avg_age_days"),
        )
        .where(
            and_(
                Appeal.run_id == run_id,
                Appeal.is_problem == True,
                Appeal.date_closed.is_(None),
                Appeal.date_created.isnot(None),
            )
        )
        .group_by(Appeal.municipality)
        .order_by(func.avg(func.extract("epoch", func.now() - Appeal.date_created) / 86400).desc())
        .limit(10)
    )
    long_open = [
        {
            "municipality": row.municipality,
            "count": row.count,
            "avg_age_days": round(float(row.avg_age_days or 0), 1),
        }
        for row in long_open_q.all()
    ]

    return {
        "top_categories": top_categories,
        "top_municipalities": top_municipalities,
        "category_municipality_matrix": matrix,
        "severity_by_category": severity_by_category,
        "long_open": long_open,
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
