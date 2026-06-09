"""Alerts: новые кластеры и резкий рост по сравнению с предыдущим run."""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.storage.database import get_db
from backend.storage.models import ProblemCluster, ProcessingRun

router = APIRouter()

GROWTH_THRESHOLD = 1.5  # +50% к предыдущему — считается ростом
NEW_MIN_APPEALS = 5      # новый кластер «достаточно крупный» от стольких обращений


def _similar_name(a: str, b: str) -> bool:
    """Очень грубое сравнение названий кластеров: совпадение по нормализованной форме."""
    if not a or not b:
        return False
    na = " ".join(a.lower().split())
    nb = " ".join(b.lower().split())
    if na == nb:
        return True
    # Префиксное совпадение (отрезаем разную часть)
    short = min(len(na), len(nb), 40)
    return na[:short] == nb[:short]


@router.get("/alerts/{run_id}")
async def get_alerts(run_id: int, db: AsyncSession = Depends(get_db)):
    """Алерты по сравнению с предыдущим completed run."""
    cur_q = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    cur = cur_q.scalar_one_or_none()
    if not cur:
        raise HTTPException(404, "Run not found")

    # Предыдущий completed run
    prev_q = await db.execute(
        select(ProcessingRun)
        .where(
            and_(
                ProcessingRun.user_id == cur.user_id,
                ProcessingRun.id < run_id,
                ProcessingRun.status == "completed",
            )
        )
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )
    prev = prev_q.scalar_one_or_none()

    cur_clusters_q = await db.execute(
        select(ProblemCluster).where(ProblemCluster.run_id == run_id)
    )
    cur_clusters = cur_clusters_q.scalars().all()

    if not prev:
        # Первый run — все крупные кластеры считаем NEW
        return {
            "has_previous": False,
            "previous_run_id": None,
            "alerts": [
                {
                    "type": "NEW",
                    "cluster_id": c.id,
                    "municipality": c.municipality,
                    "category": c.category,
                    "name": c.cluster_name,
                    "appeal_count": c.appeal_count,
                    "previous_count": 0,
                    "growth_x": None,
                }
                for c in cur_clusters if (c.appeal_count or 0) >= NEW_MIN_APPEALS
            ][:30],
        }

    prev_clusters_q = await db.execute(
        select(ProblemCluster).where(ProblemCluster.run_id == prev.id)
    )
    prev_clusters = prev_clusters_q.scalars().all()

    # Индекс предыдущих кластеров по (municipality, category, name~)
    prev_by_key: dict[tuple, list[ProblemCluster]] = defaultdict(list)
    for c in prev_clusters:
        prev_by_key[(c.municipality, c.category)].append(c)

    alerts = []
    for cur_c in cur_clusters:
        if (cur_c.appeal_count or 0) < NEW_MIN_APPEALS:
            continue
        candidates = prev_by_key.get((cur_c.municipality, cur_c.category), [])
        match = None
        for p in candidates:
            if _similar_name(cur_c.cluster_name, p.cluster_name):
                match = p
                break
        if match is None:
            alerts.append({
                "type": "NEW",
                "cluster_id": cur_c.id,
                "municipality": cur_c.municipality,
                "category": cur_c.category,
                "name": cur_c.cluster_name,
                "appeal_count": cur_c.appeal_count,
                "previous_count": 0,
                "growth_x": None,
            })
        else:
            prev_count = max(match.appeal_count or 0, 1)
            cur_count = cur_c.appeal_count or 0
            growth = cur_count / prev_count
            if growth >= GROWTH_THRESHOLD:
                alerts.append({
                    "type": "GROWING",
                    "cluster_id": cur_c.id,
                    "municipality": cur_c.municipality,
                    "category": cur_c.category,
                    "name": cur_c.cluster_name,
                    "appeal_count": cur_count,
                    "previous_count": prev_count,
                    "growth_x": round(growth, 2),
                })

    # Сортируем: GROWING выше (по growth), NEW по appeal_count
    alerts.sort(key=lambda a: (a["type"] != "GROWING", -(a.get("growth_x") or 0), -(a.get("appeal_count") or 0)))

    return {
        "has_previous": True,
        "previous_run_id": prev.id,
        "alerts": alerts[:30],
    }
