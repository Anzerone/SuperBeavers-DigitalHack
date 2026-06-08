"""Database engine and session management."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.config import DATABASE_URL, DATABASE_URL_SYNC
from backend.storage.models import Base

# Async engine for FastAPI
async_engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

# Sync engine for pipeline (background tasks)
sync_engine = create_engine(DATABASE_URL_SYNC, echo=False, pool_size=5, max_overflow=10)
SyncSessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False)


async def init_db():
    """Create all tables."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    """Dependency for FastAPI routes."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def get_sync_db():
    """Get sync session for pipeline tasks."""
    session = SyncSessionLocal()
    try:
        return session
    except Exception:
        session.close()
        raise


async def ensure_default_user():
    """Create built-in users and return admin id for legacy call sites."""
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        from backend.config import (
            DEFAULT_ADMIN_PASSWORD,
            DEFAULT_ADMIN_USERNAME,
            DEFAULT_USER_PASSWORD,
            DEFAULT_USER_USERNAME,
        )
        from backend.security import hash_password
        from backend.storage.models import User

        defaults = [
            (DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD, "admin"),
            (DEFAULT_USER_USERNAME, DEFAULT_USER_PASSWORD, "user"),
        ]
        admin_user = None
        for username, password, role in defaults:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if not user:
                user = User(username=username, hashed_password=hash_password(password), role=role)
                session.add(user)
            else:
                user.hashed_password = hash_password(password)
                user.role = role
            if role == "admin":
                admin_user = user

        await session.commit()
        await session.refresh(admin_user)
        return admin_user.id
