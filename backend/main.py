"""FastAPI application entry point."""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.storage.database import init_db, ensure_default_user
from backend.api.routes import upload, processing, dashboard, appeals, reports, chat


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
app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(processing.router, prefix="/api", tags=["processing"])
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(appeals.router, prefix="/api", tags=["appeals"])
app.include_router(reports.router, prefix="/api", tags=["reports"])
app.include_router(chat.router, prefix="/api", tags=["chat"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}
