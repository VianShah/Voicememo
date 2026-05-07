"""
VoiceInsight AI — FastAPI Application Entrypoint

Serves the API (v1 routes) and the built Vite React frontend as static files.
"""

import logging
import os
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


# ── Serve audio files (with HTTP Range / 206 support) ─────────────
settings = get_settings()

raw_audio_path = Path(settings.RAW_AUDIO_DIR)
raw_audio_path.mkdir(parents=True, exist_ok=True)

from fastapi import Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi import HTTPException
from app.services.storage import get_storage_service


def _range_streaming_response(file_path: Path, media_type: str, request: Request):
    """
    Return a StreamingResponse that honours the HTTP ``Range`` header.
    Without this, browser <audio> elements stall on 206 because
    FastAPI's plain FileResponse does not implement byte-range serving.
    """
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    # ── No Range header → serve the whole file ─────────────────────
    if not range_header:
        def full_iter():
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk
        return StreamingResponse(
            full_iter(),
            status_code=200,
            media_type=media_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    # ── Parse Range header (e.g. "bytes=0-1023") ───────────────────
    try:
        unit, ranges = range_header.split("=", 1)
        start_str, end_str = ranges.split("-", 1)
        start = int(start_str) if start_str.strip() else 0
        end   = int(end_str)   if end_str.strip()   else file_size - 1
    except (ValueError, TypeError):
        raise HTTPException(status_code=416, detail="Invalid Range header")

    if start > end or end >= file_size:
        raise HTTPException(
            status_code=416,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = end - start + 1

    def range_iter():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = chunk_size
            while remaining > 0:
                data = f.read(min(65536, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        range_iter(),
        status_code=206,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(chunk_size),
        },
    )


@app.get("/v1/recordings/{filename:path}")
async def serve_recording(filename: str, request: Request):
    """Serve a raw WAV recording with proper byte-range support."""
    bare_filename = os.path.basename(filename)
    storage = get_storage_service()
    if storage.is_s3_enabled:
        url = storage.generate_presigned_url(f"raw/{bare_filename}")
        if not url:
            raise HTTPException(status_code=404, detail="Recording not found")
        return RedirectResponse(url)
    file_path = raw_audio_path / bare_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Recording not found")
    return _range_streaming_response(file_path, "audio/wav", request)


@app.get("/v1/snippets/{filename:path}")
async def serve_snippet(filename: str, request: Request):
    """Serve an MP3 snippet with proper byte-range support."""
    snippets_path = Path(settings.SNIPPETS_DIR)
    snippets_path.mkdir(parents=True, exist_ok=True)
    bare_filename = os.path.basename(filename)
    storage = get_storage_service()
    if storage.is_s3_enabled:
        url = storage.generate_presigned_url(f"snippets/{bare_filename}")
        if not url:
            raise HTTPException(status_code=404, detail="Snippet not found")
        return RedirectResponse(url)
    file_path = snippets_path / bare_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Snippet not found")
    return _range_streaming_response(file_path, "audio/mpeg", request)


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
