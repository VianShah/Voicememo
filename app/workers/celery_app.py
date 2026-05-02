"""
VoiceInsight AI — Celery Application Configuration
"""

from celery import Celery
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "voiceinsight",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    # ── Task serialization ──────────────────────────────────────────
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # ── Timeouts ────────────────────────────────────────────────────
    task_soft_time_limit=600,      # 10 min soft limit
    task_time_limit=900,           # 15 min hard kill

    # ── Worker concurrency ──────────────────────────────────────────
    worker_concurrency=2,          # Whisper is CPU-heavy, don't oversubscribe
    worker_prefetch_multiplier=1,  # One task at a time per worker

    # ── Result expiry ───────────────────────────────────────────────
    result_expires=3600,           # Clean up results after 1 hour

    # ── Task discovery ──────────────────────────────────────────────
    include=["app.workers.tasks"],
)
