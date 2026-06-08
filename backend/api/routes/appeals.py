"""Appeals and clusters endpoints with filtering, pagination, export."""
import io
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import Optional
import openpyxl

from backend.labels import format_severity
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
    filters = [Appeal.run_id == run_id]
    for clause in (_multi(Appeal.municipality, municipality), _multi(Appeal.severity, severity), _multi(Appeal.category, category)):
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
    filters = _build_appeal_filter(run_id, municipality, severity, category, is_problem, cluster_id)
    if search:
        like = f"%{search}%"
        filters.append(or_(
            Appeal.incident_text.ilike(like),
            Appeal.municipality.ilike(like),
            Appeal.category.ilike(like),
        ))

    count_q = await db.execute(select(func.count()).where(and_(*filters)).select_from(Appeal))
    total = count_q.scalar()

    offset = (page - 1) * page_size
    data_q = await db.execute(
        select(Appeal)
        .where(and_(*filters))
        .order_by(Appeal.id)
        .offset(offset)
        .limit(page_size)
    )
    appeals = data_q.scalars().all()

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
                "severity": a.severity,
                "category": a.category,
                "is_problem": a.is_problem,
                "confidence": a.confidence,
            }
            for a in appeals
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
    filters = [ProblemCluster.run_id == run_id]
    for clause in (_multi(ProblemCluster.municipality, municipality), _multi(ProblemCluster.severity, severity), _multi(ProblemCluster.category, category)):
        if clause is not None:
            filters.append(clause)
    if search:
        like = f"%{search}%"
        filters.append(or_(
            ProblemCluster.cluster_name.ilike(like),
            ProblemCluster.municipality.ilike(like),
            ProblemCluster.category.ilike(like),
        ))

    count_q = await db.execute(select(func.count()).where(and_(*filters)).select_from(ProblemCluster))
    total = count_q.scalar()

    offset = (page - 1) * page_size
    data_q = await db.execute(
        select(ProblemCluster)
        .where(and_(*filters))
        .order_by(ProblemCluster.appeal_count.desc())
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
                "cluster_name": c.cluster_name,
                "municipality": c.municipality,
                "category": c.category,
                "severity": c.severity,
                "appeal_count": c.appeal_count,
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
    filters = _build_appeal_filter(run_id, municipality, severity, category, is_problem, cluster_id)

    data_q = await db.execute(
        select(Appeal).where(and_(*filters)).order_by(Appeal.id).limit(50000)
    )
    appeals = data_q.scalars().all()

    wb = openpyxl.Workbook()

    ws_sum = wb.active
    ws_sum.title = "Сводка"
    ws_sum.append(["Параметр", "Значение"])
    ws_sum.append(["Всего записей", len(appeals)])
    sev_counts = {}
    cat_counts = {}
    for a in appeals:
        sev_counts[a.severity] = sev_counts.get(a.severity, 0) + 1
        cat_counts[a.category] = cat_counts.get(a.category, 0) + 1
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
    for i, a in enumerate(appeals, 1):
        ws_data.append([
            i, a.municipality,
            (a.incident_text or "")[:500],
            a.group_name, a.incident_type, a.outcome,
            format_severity(a.severity), a.category,
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=appeals_export.xlsx"},
    )
