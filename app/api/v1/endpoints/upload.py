"""
VoiceInsight AI — Upload Endpoint

POST /v1/upload — Receives audio file, creates a task, and enqueues processing.
"""

import os
import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas.insight import UploadResponse

logger = logging.getLogger("voiceinsight.api.upload")
router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
async def upload_audio(audio: UploadFile = File(...)):
    """
    Upload an audio file for processing.

    Accepts MP3, WAV, M4A, WebM audio files.
    Returns a task_id immediately — the actual processing happens
    asynchronously in a Celery worker.
    """
    settings = get_settings()

    # Validate file
    if not audio.filename:
        raise HTTPException(status_code=400, detail="No audio file uploaded")

    allowed_extensions = {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".flac"}
    ext = Path(audio.filename).suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: {ext}. Supported: {', '.join(allowed_extensions)}",
        )

    # Generate task ID
    task_id = f"task-{uuid.uuid4().hex[:12]}"

    # Save uploaded file to disk
    raw_dir = Path(settings.RAW_AUDIO_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)
    file_path = str(raw_dir / f"{task_id}_upload{ext}")

    logger.info("[%s] Receiving upload: %s (%.2f MB)", task_id, audio.filename, (audio.size or 0) / 1024 / 1024)

    contents = await audio.read()
    with open(file_path, "wb") as f:
        f.write(contents)

    logger.info("[%s] Saved to %s", task_id, file_path)

    # Create initial DB record (pending)
    _create_pending_record(task_id, settings)

    # Enqueue Celery task
    from app.workers.tasks import process_audio_task
    process_audio_task.delay(task_id, file_path)
    logger.info("[%s] Task enqueued to Celery", task_id)

    return UploadResponse(task_id=task_id)


def _create_pending_record(task_id: str, settings):
    """Create an initial 'pending' insight record in the database."""
    from app.models.insight import Insight

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    if "asyncpg" in sync_url:
        sync_url = sync_url.replace("asyncpg", "psycopg2")

    engine = create_engine(sync_url)
    with Session(engine) as session:
        insight = Insight(task_id=task_id, status="pending", progress=0.0)
        session.add(insight)
        session.commit()
    engine.dispose()
