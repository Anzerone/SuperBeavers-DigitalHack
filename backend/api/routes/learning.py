"""Active learning loop: ручная разметка low-confidence записей."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.display_fields import category_value, severity_value
from backend.config import CATEGORIES, OUTPUT_DIR, SEVERITIES
from backend.storage.database import get_db
from backend.storage.models import Appeal

router = APIRouter()

ANNOTATIONS_PATH = Path(OUTPUT_DIR) / "annotations.jsonl"


def _load_custom_categories() -> list[str]:
    """Категории, добавленные разметчиками вручную (нет в базовом списке)."""
    known = set(CATEGORIES)
    custom: list[str] = []
    if not ANNOTATIONS_PATH.exists():
        return custom
    try:
        with open(ANNOTATIONS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    category = str(json.loads(line).get("annotated", {}).get("category") or "").strip()
                except Exception:
                    continue
                if category and category not in known and category not in custom:
                    custom.append(category)
    except Exception:
        pass
    return sorted(custom)


def _load_annotated_ids() -> set[int]:
    """Множество уже размеченных appeal_id."""
    ids: set[int] = set()
    if not ANNOTATIONS_PATH.exists():
        return ids
    try:
        with open(ANNOTATIONS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if "appeal_id" in obj:
                        ids.add(int(obj["appeal_id"]))
                except Exception:
                    continue
    except Exception:
        pass
    return ids


@router.get("/learning/{run_id}/queue")
async def get_learning_queue(
    run_id: int,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Топ-N low-confidence appeals для разметки."""
    annotated = _load_annotated_ids()
    res = await db.execute(
        select(Appeal)
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .order_by(Appeal.confidence.asc().nullsfirst())
        .limit(limit * 3)
    )
    appeals = res.scalars().all()

    queue = []
    for a in appeals:
        if a.id in annotated:
            continue
        queue.append({
            "appeal_id": a.id,
            "incident_text": a.incident_text,
            "municipality": a.municipality,
            "group_name": a.group_name,
            "predicted_category": category_value(a.category, a.group_name),
            "predicted_severity": severity_value(a.severity),
            "confidence": round(a.confidence or 0, 3),
        })
        if len(queue) >= limit:
            break

    return {
        "queue": queue,
        "annotated_count": len(annotated),
        # Базовый справочник + категории, добавленные разметчиками вручную.
        "categories": CATEGORIES + _load_custom_categories(),
        "severities": SEVERITIES,
    }


@router.post("/learning/annotate")
async def annotate(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Сохранить ручную разметку: appeal_id + true_category + true_severity + is_problem."""
    appeal_id = payload.get("appeal_id")
    if not appeal_id:
        raise HTTPException(400, "appeal_id required")

    appeal_q = await db.execute(select(Appeal).where(Appeal.id == appeal_id))
    appeal = appeal_q.scalar_one_or_none()
    if not appeal:
        raise HTTPException(404, "Appeal not found")

    record = {
        "appeal_id": int(appeal_id),
        "run_id": appeal.run_id,
        "text": appeal.incident_text,
        "group_name": appeal.group_name,
        "predicted": {
            "category": category_value(appeal.category, appeal.group_name),
            "severity": severity_value(appeal.severity),
            "is_problem": appeal.is_problem,
        },
        "annotated": {
            "category": payload.get("category"),
            "severity": payload.get("severity"),
            "is_problem": payload.get("is_problem", True),
        },
        "annotator": payload.get("annotator", "anon"),
        "ts": time.time(),
    }

    ANNOTATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ANNOTATIONS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"ok": True, "saved_to": str(ANNOTATIONS_PATH)}


@router.get("/learning/stats")
async def learning_stats():
    """Сколько примеров уже размечено, по категориям."""
    annotated = _load_annotated_ids()
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    if ANNOTATIONS_PATH.exists():
        with open(ANNOTATIONS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    cat = obj.get("annotated", {}).get("category")
                    sev = obj.get("annotated", {}).get("severity")
                    if cat:
                        by_category[cat] = by_category.get(cat, 0) + 1
                    if sev:
                        by_severity[sev] = by_severity.get(sev, 0) + 1
                except Exception:
                    pass

    return {
        "total": len(annotated),
        "by_category": by_category,
        "by_severity": by_severity,
        "path": str(ANNOTATIONS_PATH),
    }
