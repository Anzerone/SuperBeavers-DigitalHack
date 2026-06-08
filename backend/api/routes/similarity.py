"""Similar appeals search via in-memory TF-IDF.

Для прода имеет смысл перейти на pgvector + cosine ANN на embedding'ах,
но для прототипа на ~50к строк TF-IDF на лету быстрее в развёртывании
(не требует расширения Postgres) и даёт хорошие результаты для близких
текстовых дублей.
"""
from __future__ import annotations

import threading
import time

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.labels import format_severity
from backend.storage.database import get_db
from backend.storage.models import Appeal

router = APIRouter()

# Per-run TF-IDF index cache. Структура: { run_id: {"vec": TfidfVectorizer, "matrix": csr, "ids": list, "ts": time} }
_INDEX_CACHE: dict[int, dict] = {}
_INDEX_LOCK = threading.Lock()
_INDEX_TTL_SECONDS = 60 * 60  # пересобираем не чаще раза в час


def _expire_old() -> None:
    """Удалить индексы старше TTL — экономим память."""
    now = time.time()
    expired = [k for k, v in _INDEX_CACHE.items() if now - v["ts"] > _INDEX_TTL_SECONDS]
    for k in expired:
        _INDEX_CACHE.pop(k, None)


async def _build_index(db: AsyncSession, run_id: int) -> dict | None:
    """Построить TF-IDF индекс для run, если ещё не построен."""
    with _INDEX_LOCK:
        if run_id in _INDEX_CACHE:
            return _INDEX_CACHE[run_id]

    res = await db.execute(
        select(Appeal.id, Appeal.incident_text)
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .order_by(Appeal.id)
    )
    rows = res.all()
    if not rows:
        return None

    ids = [r.id for r in rows]
    texts = [r.incident_text or "" for r in rows]

    vec = TfidfVectorizer(
        max_features=50_000,
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.85,
        sublinear_tf=True,
    )
    matrix = vec.fit_transform(texts)

    entry = {"vec": vec, "matrix": matrix, "ids": ids, "ts": time.time()}
    with _INDEX_LOCK:
        _expire_old()
        _INDEX_CACHE[run_id] = entry
    return entry


@router.get("/appeals/{appeal_id}/similar")
async def find_similar(
    appeal_id: int,
    k: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Топ-K похожих обращений по TF-IDF cosine similarity."""
    appeal_q = await db.execute(select(Appeal).where(Appeal.id == appeal_id))
    appeal = appeal_q.scalar_one_or_none()
    if not appeal:
        raise HTTPException(404, "Appeal not found")

    index = await _build_index(db, appeal.run_id)
    if not index:
        return {"items": [], "appeal_id": appeal_id}

    ids: list[int] = index["ids"]
    matrix = index["matrix"]
    vec: TfidfVectorizer = index["vec"]

    try:
        pos = ids.index(appeal_id)
        query_vec = matrix[pos]
    except ValueError:
        # appeal не в индексе (например, не-проблема) — fallback на transform
        query_vec = vec.transform([appeal.incident_text or ""])

    sims = cosine_similarity(query_vec, matrix).ravel()

    # top-K, исключая сам appeal
    top_idx = np.argsort(-sims)
    top_idx = [i for i in top_idx if ids[i] != appeal_id][:k]

    if not top_idx:
        return {"items": [], "appeal_id": appeal_id}

    pick_ids = [ids[i] for i in top_idx]
    data_q = await db.execute(select(Appeal).where(Appeal.id.in_(pick_ids)))
    by_id = {a.id: a for a in data_q.scalars().all()}

    items = []
    for i in top_idx:
        a = by_id.get(ids[i])
        if not a:
            continue
        items.append({
            "id": a.id,
            "similarity": round(float(sims[i]), 4),
            "municipality": a.municipality,
            "category": a.category,
            "severity": format_severity(a.severity) if a.severity else None,
            "group_name": a.group_name,
            "incident_text": a.incident_text or "",
            "outcome": a.outcome,
        })

    return {
        "appeal_id": appeal_id,
        "source_text": appeal.incident_text or "",
        "items": items,
    }


@router.post("/similarity/{run_id}/rebuild")
async def rebuild_index(run_id: int, db: AsyncSession = Depends(get_db)):
    """Принудительно пересобрать индекс (после изменения данных)."""
    with _INDEX_LOCK:
        _INDEX_CACHE.pop(run_id, None)
    index = await _build_index(db, run_id)
    if not index:
        return {"ok": False, "size": 0}
    return {"ok": True, "size": len(index["ids"])}
