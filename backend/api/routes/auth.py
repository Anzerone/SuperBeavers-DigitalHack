"""Authentication endpoints."""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.security import create_access_token, verify_password
from backend.storage.database import get_db
from backend.storage.models import User

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
    }


@router.post("/auth/login")
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == payload.username.strip()))
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")

    token = create_access_token({"sub": str(user.id), "username": user.username, "role": user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _user_payload(user),
    }


@router.get("/auth/me")
async def me(user: User = Depends(get_current_user)):
    return _user_payload(user)
