"""
VoiceInsight AI — FastAPI Application Entrypoint

Serves the API (v1 routes) and the built Vite React frontend as static files.
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.core.database import init_db, close_db
from app.api.v1.router import router as v1_router

# ── Configure structured logging ────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("voiceinsight")


# ── Application lifespan ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    settings = get_settings()
    logger.info("Starting VoiceInsight AI...")
    logger.info("LLM Provider: %s", settings.LLM_PROVIDER)
    logger.info("Gemini Model: %s", settings.GEMINI_MODEL)
    logger.info("Storage: %s", settings.STORAGE_DIR)
    logger.info("Database: %s", settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else "configured")

    # Create database tables
    await init_db()
    logger.info("Database initialized")

    yield

    # Shutdown
    await close_db()
    logger.info("VoiceInsight AI shutdown complete")


# ── Create FastAPI app ──────────────────────────────────────────────
app = FastAPI(
    title="VoiceInsight AI",
    description="Transform voice recordings into searchable, queryable Audio Insights.",
    version="2.0.0",
    lifespan=lifespan,
)

# ── CORS ────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ──────────────────────────────────────────────────────
app.include_router(v1_router)


# ── Health check ────────────────────────────────────────────────────
@app.get("/health")
async def health_check():
    """Health check with system diagnostics."""
    import psutil
    settings = get_settings()

    return {
        "status": "ok",
        "service": "voiceinsight-api",
        "version": "2.0.0",
        "llm_provider": settings.LLM_PROVIDER,
        "gemini_model": settings.GEMINI_MODEL,
        "storage_dir": settings.STORAGE_DIR,
        "has_gemini_key": bool(settings.GEMINI_API_KEY),
        "has_pinecone_key": bool(settings.PINECONE_API_KEY),
    }


# ── Serve audio files ──────────────────────────────────────────────
settings = get_settings()

# Serve raw recordings
raw_audio_path = Path(settings.RAW_AUDIO_DIR)
raw_audio_path.mkdir(parents=True, exist_ok=True)
from fastapi.responses import RedirectResponse
from fastapi import HTTPException
from app.services.storage import get_storage_service

@app.get("/v1/recordings/{filename}")
async def serve_recording(filename: str):
    storage = get_storage_service()
    if storage.is_s3_enabled:
        url = storage.generate_presigned_url(f"raw/{filename}")
        if not url:
            raise HTTPException(status_code=404, detail="Recording not found")
        return RedirectResponse(url)
    else:
        file_path = raw_audio_path / filename
        if file_path.exists():
            return FileResponse(str(file_path))
        raise HTTPException(status_code=404, detail="Recording not found")

@app.get("/v1/snippets/{filename}")
async def serve_snippet(filename: str):
    snippets_path = Path(settings.SNIPPETS_DIR)
    snippets_path.mkdir(parents=True, exist_ok=True)
    
    storage = get_storage_service()
    if storage.is_s3_enabled:
        url = storage.generate_presigned_url(f"snippets/{filename}")
        if not url:
            raise HTTPException(status_code=404, detail="Snippet not found")
        return RedirectResponse(url)
    else:
        file_path = snippets_path / filename
        if file_path.exists():
            return FileResponse(str(file_path))
        raise HTTPException(status_code=404, detail="Snippet not found")


# ── Serve Vite React frontend (production) ──────────────────────────
dist_path = Path("dist")
if dist_path.exists():
    app.mount("/assets", StaticFiles(directory=str(dist_path / "assets")), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve the React SPA for any non-API route."""
        file_path = dist_path / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(dist_path / "index.html"))
