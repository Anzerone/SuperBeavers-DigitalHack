"""Appeals and clusters endpoints with filtering, pagination, export."""
import io
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import Optional
import openpyxl

from backend.api.display_fields import DEFAULT_CATEGORY, appeal_display_category, appeal_display_severity
from backend.labels import format_severity
from backend.problem_naming import display_problem_name
from backend.storage.database import get_db
from backend.storage.models import Appeal, ProblemCluster, AppealClusterMap

router = APIRouter()


# Разделитель мультивыбора — Unit Separator (\x1f), которого нет в значениях
# (запятая встречается внутри значений, напр. "Омская область, другое").
FILTER_SEP = "\x1f"


def _multi(column, value):
    """Условие фильтра с поддержкой мультивыбора."""
    if not value:
        return None
    parts = [p.strip() for p in str(value).split(FILTER_SEP) if p.strip()]
    if not parts:
        return None
    return column == parts[0] if len(parts) == 1 else column.in_(parts)


def _build_appeal_filter(run_id, municipality, severity, category, is_problem, cluster_id):
    display_severity = appeal_display_severity()
    display_category = appeal_display_category()
    filters = [Appeal.run_id == run_id]
    for clause in (
        _multi(Appeal.municipality, municipality),
        _multi(display_severity, severity),
        _multi(display_category, category),
    ):
        if clause is not None:
            filters.append(clause)
    if is_problem is not None:
        filters.append(Appeal.is_problem == is_problem)
    if cluster_id:
        filters.append(
            Appeal.id.in_(
                select(AppealClusterMap.appeal_id).where(AppealClusterMap.cluster_id == cluster_id)
            )
        )
    return filters


def _build_cluster_filter(run_id, municipality, severity, category):
    display_category = func.coalesce(func.nullif(ProblemCluster.category, ""), DEFAULT_CATEGORY)
    filters = [ProblemCluster.run_id == run_id]
    for clause in (
        _multi(ProblemCluster.municipality, municipality),
        _multi(ProblemCluster.severity, severity),
        _multi(display_category, category),
    ):
        if clause is not None:
            filters.append(clause)
    return filters


@router.get("/appeals/{run_id}")
async def get_appeals(
    run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=100),
    municipality: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    is_problem: Optional[bool] = None,
    cluster_id: Optional[int] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    display_severity = appeal_display_severity().label("display_severity")
    display_category = appeal_display_category().label("display_category")
    filters = _build_appeal_filter(run_id, municipality, severity, category, is_problem, cluster_id)
    if search:
        like = f"%{search}%"
        filters.append(or_(
            Appeal.incident_text.ilike(like),
            Appeal.municipality.ilike(like),
            display_category.ilike(like),
        ))

    count_q = await db.execute(select(func.count()).where(and_(*filters)).select_from(Appeal))
    total = count_q.scalar()

    offset = (page - 1) * page_size
    data_q = await db.execute(
        select(Appeal, display_severity, display_category)
        .where(and_(*filters))
        .order_by(Appeal.id)
        .offset(offset)
        .limit(page_size)
    )
    rows = data_q.all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "items": [
            {
                "id": a.id,
                "incident_text": a.incident_text[:300] if a.incident_text else "",
                "full_text": a.incident_text,
                "municipality": a.municipality,
                "group_name": a.group_name,
                "incident_type": a.incident_type,
                "outcome": a.outcome,
                "severity": display_severity,
                "category": display_category,
                "is_problem": a.is_problem,
                "confidence": a.confidence,
            }
            for a, display_severity, display_category in rows
        ],
    }


@router.get("/clusters/{run_id}")
async def get_clusters(
    run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=100),
    municipality: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    filters = _build_cluster_filter(run_id, municipality, severity, category)
    if search:
        like = f"%{search}%"
        filters.append(or_(
            ProblemCluster.cluster_name.ilike(like),
            ProblemCluster.description.ilike(like),
            ProblemCluster.centroid_text.ilike(like),
            ProblemCluster.municipality.ilike(like),
            ProblemCluster.category.ilike(like),
        ))

    count_q = await db.execute(
        select(func.count()).where(and_(*filters)).select_from(ProblemCluster)
    )
    total = count_q.scalar()

    offset = (page - 1) * page_size
    data_q = await db.execute(
        select(ProblemCluster)
        .where(and_(*filters))
        .order_by(ProblemCluster.appeal_count.desc(), ProblemCluster.rank)
        .offset(offset)
        .limit(page_size)
    )
    clusters = data_q.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "items": [
            {
                "id": c.id,
                "cluster_name": display_problem_name(
                    c.cluster_name,
                    c.category,
                    c.description,
                    c.centroid_text,
                    c.example_texts,
                ),
                "municipality": c.municipality,
                "category": c.category or DEFAULT_CATEGORY,
                "severity": c.severity or "MEDIUM",
                "cluster_severity": c.severity,
                "appeal_count": c.appeal_count,
                "total_appeal_count": c.appeal_count,
                "rank": c.rank,
                "description": c.description,
                "centroid_text": c.centroid_text,
                "example_texts": c.example_texts,
            }
            for c in clusters
        ],
    }


@router.get("/appeals/{run_id}/export")
async def export_appeals(
    run_id: int,
    municipality: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    is_problem: Optional[bool] = None,
    cluster_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    display_severity = appeal_display_severity().label("display_severity")
    display_category = appeal_display_category().label("display_category")
    filters = _build_appeal_filter(run_id, municipality, severity, category, is_problem, cluster_id)

    data_q = await db.execute(
        select(Appeal, display_severity, display_category).where(and_(*filters)).order_by(Appeal.id).limit(50000)
    )
    rows = data_q.all()

    wb = openpyxl.Workbook()

    ws_sum = wb.active
    ws_sum.title = "Сводка"
    ws_sum.append(["Параметр", "Значение"])
    ws_sum.append(["Всего записей", len(rows)])
    sev_counts = {}
    cat_counts = {}
    for a, sev, cat in rows:
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    ws_sum.append([])
    ws_sum.append(["Тяжесть", "Количество"])
    for s, c in sorted(sev_counts.items(), key=lambda x: -x[1]):
        ws_sum.append([format_severity(s), c])
    ws_sum.append([])
    ws_sum.append(["Категория", "Количество"])
    for s, c in sorted(cat_counts.items(), key=lambda x: -x[1]):
        ws_sum.append([s or "Не определено", c])

    ws_data = wb.create_sheet("Обращения")
    ws_data.append(["#", "Район", "Текст обращения", "Группа тем", "Тип инцидента", "Итог", "Тяжесть", "Категория"])
    for i, (a, sev, cat) in enumerate(rows, 1):
        ws_data.append([
            i, a.municipality,
            (a.incident_text or "")[:500],
            a.group_name, a.incident_type, a.outcome,
            format_severity(sev), cat,
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=appeals.xlsx; filename*=UTF-8''%D0%92%D1%8B%D0%B3%D1%80%D1%83%D0%B7%D0%BA%D0%B0%20%D0%BE%D0%B1%D1%80%D0%B0%D1%89%D0%B5%D0%BD%D0%B8%D0%B9.xlsx"},
    )
