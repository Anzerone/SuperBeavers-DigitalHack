"""FastAPI application entry point."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.deps import get_current_user, require_roles
from backend.storage.database import init_db, ensure_default_user
from backend.api.routes import auth, upload, processing, dashboard, appeals, reports, chat, alerts, similarity, learning, presets


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    await init_db()
    default_user_id = await ensure_default_user()
    app.state.default_user_id = default_user_id
    # Ensure upload dirs exist
    from backend.config import UPLOAD_DIR, OUTPUT_DIR
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    yield


app = FastAPI(
    title="Классификатор обращений граждан",
    description="Омская область — аналитика обращений на базе LLM",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
auth_required = [Depends(get_current_user)]

app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(upload.router, prefix="/api", tags=["upload"], dependencies=auth_required)
app.include_router(processing.router, prefix="/api", tags=["processing"], dependencies=auth_required)
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"], dependencies=auth_required)
app.include_router(appeals.router, prefix="/api", tags=["appeals"], dependencies=auth_required)
app.include_router(reports.router, prefix="/api", tags=["reports"], dependencies=auth_required)
app.include_router(chat.router, prefix="/api", tags=["chat"], dependencies=auth_required)
app.include_router(alerts.router, prefix="/api", tags=["alerts"], dependencies=auth_required)
app.include_router(similarity.router, prefix="/api", tags=["similarity"], dependencies=auth_required)
app.include_router(presets.router, prefix="/api", tags=["presets"], dependencies=auth_required)
app.include_router(learning.router, prefix="/api", tags=["learning"], dependencies=[Depends(require_roles("admin"))])


@app.get("/api/health")
async def health():
    return {"status": "ok"}
