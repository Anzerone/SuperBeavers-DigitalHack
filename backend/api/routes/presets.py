"""Per-user dashboard filter presets."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.storage.database import get_db
from backend.storage.models import FilterPreset, User

router = APIRouter()


class PresetPayload(BaseModel):
    name: str
    filters: dict[str, Any] = Field(default_factory=dict)


def _preset_payload(preset: FilterPreset) -> dict[str, Any]:
    stamp = preset.updated_at or preset.created_at
    return {
        "id": preset.id,
        "name": preset.name,
        "filters": preset.filters or {},
        "ts": int(stamp.timestamp() * 1000) if stamp else 0,
    }


@router.get("/presets")
async def list_presets(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(FilterPreset)
        .where(FilterPreset.user_id == current_user.id)
        .order_by(FilterPreset.updated_at.desc(), FilterPreset.id.desc())
    )
    return {"presets": [_preset_payload(preset) for preset in result.scalars().all()]}


@router.post("/presets")
async def save_preset(
    payload: PresetPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Preset name required")

    result = await db.execute(
        select(FilterPreset).where(
            and_(FilterPreset.user_id == current_user.id, FilterPreset.name == name)
        )
    )
    preset = result.scalar_one_or_none()
    if preset:
        preset.filters = payload.filters or {}
    else:
        preset = FilterPreset(user_id=current_user.id, name=name, filters=payload.filters or {})
        db.add(preset)

    await db.commit()
    await db.refresh(preset)
    return _preset_payload(preset)


@router.delete("/presets/{preset_id}")
async def delete_preset(
    preset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(FilterPreset).where(
            and_(FilterPreset.id == preset_id, FilterPreset.user_id == current_user.id)
        )
    )
    preset = result.scalar_one_or_none()
    if not preset:
        raise HTTPException(404, "Preset not found")

    await db.delete(preset)
    await db.commit()
    return {"ok": True}
