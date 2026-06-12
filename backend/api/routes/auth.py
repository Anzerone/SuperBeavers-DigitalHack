"""Authentication endpoints."""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, require_roles
from backend.security import create_access_token, hash_password, verify_password
from backend.storage.database import get_db
from backend.storage.models import User

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


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


@router.get("/auth/users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    result = await db.execute(select(User).order_by(User.created_at.desc(), User.id.desc()))
    return {"users": [_user_payload(user) | {"created_at": user.created_at.isoformat() if user.created_at else None} for user in result.scalars().all()]}


@router.post("/auth/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    username = payload.username.strip()
    password = payload.password
    role = payload.role.strip().lower()
    if len(username) < 3:
        raise HTTPException(400, "Логин должен быть не короче 3 символов")
    if len(password) < 6:
        raise HTTPException(400, "Пароль должен быть не короче 6 символов")
    if role not in {"admin", "user"}:
        raise HTTPException(400, "Роль должна быть admin или user")

    existing_q = await db.execute(select(User).where(User.username == username))
    if existing_q.scalar_one_or_none():
        raise HTTPException(409, "Пользователь с таким логином уже существует")

    user = User(username=username, hashed_password=hash_password(password), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"user": _user_payload(user)}
