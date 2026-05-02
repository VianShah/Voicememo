"""
VoiceInsight AI — Celery Task Definitions

The main processing pipeline that runs inside the Celery worker:
1. Convert audio to WAV
2. Transcribe with Faster-Whisper
3. Filter filler words
4. Extract insights via LLM
5. Fuzzy-match quotes to timestamps
6. Slice audio snippets
7. Embed + upsert to Pinecone
8. Save to PostgreSQL database
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path

from app.workers.celery_app import celery_app
from app.core.config import get_settings

logger = logging.getLogger("voiceinsight.tasks")

# ── Helper: run async code from sync Celery task ────────────────────
def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _log_sys_stats(task_id: str, step_name: str, extra: str = ""):
    """Log system hardware usage and process details."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        logger.info(
            "[%s] %-25s | CPU: %5.1f%% | RAM: %5.1f%% | %s",
            task_id, step_name, cpu, ram, extra
        )
    except Exception:
        logger.info("[%s] %-25s | %s", task_id, step_name, extra)


def _update_task_status(task_id: str, status: str, progress: float, **kwargs):
    """Update the task status in the database."""
    from sqlalchemy import update
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models.insight import Insight
    from app.core.config import get_settings

    settings = get_settings()
    # Use synchronous URL for Celery (replace asyncpg with psycopg2)
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2").replace("postgresql+psycopg2", "postgresql+psycopg2")
    if "asyncpg" in sync_url:
        sync_url = sync_url.replace("asyncpg", "psycopg2")
    if "+psycopg2" not in sync_url and "postgresql://" in sync_url:
        sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://")

    engine = create_engine(sync_url)
    with Session(engine) as session:
        stmt = (
            update(Insight)
            .where(Insight.task_id == task_id)
            .values(status=status, progress=progress, **kwargs)
        )
        session.execute(stmt)
        session.commit()
    engine.dispose()


@celery_app.task(bind=True, name="process_audio")
def process_audio_task(self, task_id: str, file_path: str):
    """
    Main audio processing pipeline.

    This runs inside the Celery worker process, where the Whisper model
    is loaded as a singleton (never inside the FastAPI process).
    """
    settings = get_settings()

    try:
        # ── Step 1: Convert to WAV ──────────────────────────────────
        orig_size = os.path.getsize(file_path) / (1024 * 1024) if os.path.exists(file_path) else 0
        _log_sys_stats(task_id, "Audio Conversion Start", f"Input file size: {orig_size:.2f} MB")
        _update_task_status(task_id, "converting", 5.0)

        from app.services.audio_engine import convert_to_wav
        wav_path = convert_to_wav(file_path)
        wav_size = os.path.getsize(wav_path) / (1024 * 1024) if os.path.exists(wav_path) else 0
        _log_sys_stats(task_id, "Audio Conversion End", f"WAV file size: {wav_size:.2f} MB")

        # ── Step 2: Transcribe ──────────────────────────────────────
        _log_sys_stats(task_id, "Transcription Start", "Loading Whisper Model...")
        _update_task_status(task_id, "transcribing", 15.0)

        from app.services.transcription import get_transcription_service
        stt = get_transcription_service()
        result = stt.transcribe(wav_path)
        _log_sys_stats(task_id, "Transcription End", f"Duration: {result.duration:.1f}s | Words: {len(result.words)} | Lang: {result.language}")

        # ── Step 3: Filter fillers ──────────────────────────────────
        _log_sys_stats(task_id, "Filtering Start", "Identifying filler words...")
        _update_task_status(task_id, "filtering", 30.0)

        from app.services.filter_engine import FillerFilterEngine
        filler_engine = FillerFilterEngine()
        filtered = filler_engine.filter(result.words)
        _log_sys_stats(task_id, "Filtering End", f"Cleaned: {filtered.original_word_count} -> {filtered.clean_word_count} words | Saved: {filtered.token_savings_percent:.1f}% tokens")

        # ── Step 4: LLM Insight Extraction ──────────────────────────
        _log_sys_stats(task_id, "LLM Extraction Start", "Sending to LLM API (Network Bound)...")
        _update_task_status(task_id, "analyzing", 40.0)

        from app.services.llm.base import get_llm_provider
        llm = get_llm_provider()
        insight_result = _run_async(llm.extract_insights(filtered.clean_text))
        _log_sys_stats(task_id, "LLM Extraction End", f"Title: '{insight_result.title}' | Highlights: {len(insight_result.highlights)}")

        # ── Step 5: Fuzzy match timestamps ──────────────────────────
        _log_sys_stats(task_id, "Timestamp Align Start", "Fuzzy matching highlights back to audio...")
        _update_task_status(task_id, "analyzing", 55.0)

        from app.services.fuzzy_matcher import resolve_all_highlights
        resolved_highlights = resolve_all_highlights(insight_result.highlights, result.words)
        _log_sys_stats(task_id, "Timestamp Align End", f"Aligned {len(resolved_highlights)} highlights")

        # ── Step 6: Slice audio snippets ────────────────────────────
        _log_sys_stats(task_id, "Audio Slicing Start", f"Slicing {len(resolved_highlights)} MP3 snippets...")
        _update_task_status(task_id, "slicing", 70.0)

        from app.services.audio_engine import create_highlight_snippets
        snippet_results = create_highlight_snippets(
            wav_path=wav_path,
            highlights=resolved_highlights,
            snippets_dir=settings.SNIPPETS_DIR,
            insight_id=task_id,
        )

        # Map snippet URLs to highlights
        snippet_map = {s.highlight_id: s.snippet_url for s in snippet_results}
        for h in resolved_highlights:
            h["snippet_url"] = snippet_map.get(h["id"])

        _log_sys_stats(task_id, "Audio Slicing End", "Snippets successfully generated")

        # ── Step 7: Pinecone Embedding + Upsert ─────────────────────
        _log_sys_stats(task_id, "Vector Embed Start", "Preparing chunks for Pinecone (Network Bound)...")
        _update_task_status(task_id, "indexing", 80.0)

        _embed_and_upsert(task_id, result.text, insight_result)
        _log_sys_stats(task_id, "Vector Embed End", "Embeddings upserted")

        # ── Save full WAV to storage ────────────────────────────────
        audio_filename = f"{task_id}.wav"
        audio_storage_path = os.path.join(settings.RAW_AUDIO_DIR, audio_filename)
        shutil.copy2(wav_path, audio_storage_path)
        audio_url = f"/v1/recordings/{audio_filename}"

        # ── Save to database ────────────────────────────────────────
        _log_sys_stats(task_id, "Database Save Start", "Writing metadata to PostgreSQL...")
        _save_insight_to_db(
            task_id=task_id,
            transcript=result.text,
            clean_transcript=filtered.clean_text,
            insight_result=insight_result,
            resolved_highlights=resolved_highlights,
            audio_url=audio_url,
            duration=result.duration,
            filtered=filtered,
        )
        _log_sys_stats(task_id, "Database Save End", "Transactions committed")

        # ── Cleanup temp files ──────────────────────────────────────
        for f in [file_path, wav_path]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass

        _update_task_status(task_id, "completed", 100.0)
        logger.info("[%s] ✅ Pipeline complete!", task_id)

    except Exception as e:
        logger.error("[%s] ❌ Pipeline failed: %s", task_id, str(e), exc_info=True)
        _update_task_status(task_id, "failed", 0.0, error_message=str(e))
        raise


def _embed_and_upsert(task_id: str, transcript: str, insight_result):
    """Chunk, embed, and upsert transcript to Pinecone."""
    from pinecone import Pinecone
    settings = get_settings()

    if not settings.PINECONE_API_KEY:
        logger.warning("[%s] Skipping Pinecone (no API key)", task_id)
        return

    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    index = pc.Index(settings.PINECONE_INDEX)

    # Chunk the transcript
    chunks = _chunk_text(transcript)
    if not chunks:
        return

    logger.info("[%s] Embedding %d chunk(s) via Pinecone Inference...", task_id, len(chunks))

    # Batch embed all chunks in one call
    embed_result = pc.inference.embed(
        model=settings.EMBEDDING_MODEL,
        inputs=chunks,
        parameters={"input_type": "passage"},
    )

    # Build vectors
    vectors = []
    for i, embedding in enumerate(embed_result.data):
        vectors.append({
            "id": f"{task_id}-c{i}",
            "values": list(embedding.values),
            "metadata": {
                "text": chunks[i][:1000],
                "insightId": task_id,
                "title": insight_result.title,
                "summary": insight_result.summary,
                "tags": ",".join(insight_result.tags),
                "chunkIndex": i,
            },
        })

    # Batch upsert
    BATCH_SIZE = 100
    for b in range(0, len(vectors), BATCH_SIZE):
        batch = vectors[b:b + BATCH_SIZE]
        index.upsert(vectors=batch)
        logger.info("[%s] Upserted batch %d (%d vectors)", task_id, b // BATCH_SIZE + 1, len(batch))


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks by word count."""
    words = text.split()
    if not words:
        return []
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + chunk_size]))
        i += chunk_size - overlap
        if i >= len(words):
            break
    return chunks


def _save_insight_to_db(
    task_id: str,
    transcript: str,
    clean_transcript: str,
    insight_result,
    resolved_highlights: list[dict],
    audio_url: str,
    duration: float,
    filtered,
):
    """Save the insight and highlights to PostgreSQL."""
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import Session
    from app.models.insight import Insight, Highlight
    from app.core.config import get_settings

    settings = get_settings()
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    if "asyncpg" in sync_url:
        sync_url = sync_url.replace("asyncpg", "psycopg2")

    engine = create_engine(sync_url)
    with Session(engine) as session:
        # Update the existing Insight row
        stmt = (
            update(Insight)
            .where(Insight.task_id == task_id)
            .values(
                title=insight_result.title,
                summary=insight_result.summary,
                transcript=transcript,
                clean_transcript=clean_transcript,
                mood=insight_result.mood,
                tags=insight_result.tags,
                audio_url=audio_url,
                duration_seconds=float(duration),
                original_word_count=filtered.original_word_count,
                clean_word_count=filtered.clean_word_count,
                token_savings_percent=float(filtered.token_savings_percent),
                status="completed",
                progress=100.0,
            )
        )
        session.execute(stmt)

        # Get the insight ID
        insight = session.query(Insight).filter(Insight.task_id == task_id).first()

        # Create highlight rows
        for h in resolved_highlights:
            highlight = Highlight(
                insight_id=insight.id,
                text=h.get("text", ""),
                tag=h.get("tag", "#Realization"),
                start_time=float(h.get("start_time", 0.0)),
                end_time=float(h.get("end_time", 0.0)),
                snippet_url=h.get("snippet_url"),
            )
            session.add(highlight)

        session.commit()

    engine.dispose()
    logger.info("[%s] Saved insight + %d highlights to DB", task_id, len(resolved_highlights))
