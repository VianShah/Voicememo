"""
VoiceInsight AI — Celery Task Definitions

The main processing pipeline that runs inside the Celery worker:
1. Convert audio to WAV
2. Transcribe with Faster-Whisper → SAVE transcript to DB immediately
3. Filter filler words → SAVE clean_transcript to DB
4. Extract insights via LLM → SAVE title/summary/mood/tags to DB
5. Fuzzy-match quotes to timestamps
6. Slice audio snippets
7. Embed + upsert to Pinecone
8. SAVE highlights to DB → status = completed

Progressive saves ensure partial results are always available.
"""

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

from billiard.exceptions import SoftTimeLimitExceeded

from app.workers.celery_app import celery_app
from app.core.config import get_settings

logger = logging.getLogger("voiceinsight.tasks")


# ── Singleton DB Engine ─────────────────────────────────────────────
# Create ONE engine per worker process instead of per status update.
# This avoids the overhead of create_engine() + dispose() 8+ times
# per task (~2-4s saved).
_db_engine = None

def _get_engine():
    """Get or create the singleton DB engine for this worker process."""
    global _db_engine
    if _db_engine is None:
        from sqlalchemy import create_engine
        settings = get_settings()
        sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
        if "asyncpg" in sync_url:
            sync_url = sync_url.replace("asyncpg", "psycopg2")
        if "+psycopg2" not in sync_url and "postgresql://" in sync_url:
            sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://")
        _db_engine = create_engine(sync_url, pool_size=2, max_overflow=3)
    return _db_engine


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
    """Log system hardware usage and process memory footprint."""
    try:
        import psutil
        import os
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        process_mb = mem_info.rss / (1024 * 1024)
        
        cpu = psutil.cpu_percent(interval=None)
        sys_ram = psutil.virtual_memory().percent
        logger.info(
            "[%s] %-25s | CPU: %5.1f%% | SysRAM: %5.1f%% | ProcRAM: %7.1f MB | %s",
            task_id, step_name, cpu, sys_ram, process_mb, extra
        )
    except Exception:
        logger.info("[%s] %-25s | %s", task_id, step_name, extra)


# ── Progressive DB Save Helpers ─────────────────────────────────────

def _update_task_status(task_id: str, status: str, progress: float, **kwargs):
    """Update the task status in the database using the singleton engine."""
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from app.models.insight import Insight

    engine = _get_engine()
    with Session(engine) as session:
        stmt = (
            update(Insight)
            .where(Insight.task_id == task_id)
            .values(status=status, progress=progress, **kwargs)
        )
        session.execute(stmt)
        session.commit()


def _save_transcript_to_db(task_id: str, transcript: str, duration: float, audio_url: str):
    """
    Save transcript + duration immediately after transcription.

    This is the first progressive save — the user can see the transcript
    in the UI while the LLM is still running.
    """
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from app.models.insight import Insight

    engine = _get_engine()
    with Session(engine) as session:
        stmt = (
            update(Insight)
            .where(Insight.task_id == task_id)
            .values(
                transcript=transcript,
                duration_seconds=float(duration),
                audio_url=audio_url,
                status="transcribed",
                progress=30.0,
            )
        )
        session.execute(stmt)
        session.commit()
    logger.info("[%s] Progressive save: transcript (%d chars, %.1fs duration)",
                task_id, len(transcript), duration)


def _save_filtered_to_db(task_id: str, filtered):
    """
    Save clean transcript + token savings after filler filtering.
    """
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from app.models.insight import Insight

    engine = _get_engine()
    with Session(engine) as session:
        stmt = (
            update(Insight)
            .where(Insight.task_id == task_id)
            .values(
                clean_transcript=filtered.clean_text,
                original_word_count=filtered.original_word_count,
                clean_word_count=filtered.clean_word_count,
                token_savings_percent=float(filtered.token_savings_percent),
                status="filtered",
                progress=40.0,
            )
        )
        session.execute(stmt)
        session.commit()
    logger.info("[%s] Progressive save: filtered (%d → %d words, %.1f%% saved)",
                task_id, filtered.original_word_count, filtered.clean_word_count,
                filtered.token_savings_percent)


def _save_insights_to_db(task_id: str, insight_result):
    """
    Save LLM analysis (title, summary, mood, tags) after extraction.

    Saved separately from highlights so the user can see the title/summary
    while audio snippets are still being created.
    """
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from app.models.insight import Insight

    engine = _get_engine()
    with Session(engine) as session:
        stmt = (
            update(Insight)
            .where(Insight.task_id == task_id)
            .values(
                title=insight_result.title,
                summary=insight_result.summary,
                mood=insight_result.mood,
                tags=insight_result.tags,
                status="analyzed",
                progress=60.0,
            )
        )
        session.execute(stmt)
        session.commit()
    logger.info("[%s] Progressive save: insights (title='%s', mood=%s, %d tags)",
                task_id, insight_result.title, insight_result.mood, len(insight_result.tags))


def _save_highlights_to_db(task_id: str, resolved_highlights: list[dict]):
    """
    Save highlights with snippet URLs to the database.

    This is the final save — creates Highlight rows linked to the Insight.
    """
    from sqlalchemy.orm import Session
    from app.models.insight import Insight, Highlight

    engine = _get_engine()
    with Session(engine) as session:
        insight = session.query(Insight).filter(Insight.task_id == task_id).first()
        if not insight:
            logger.error("[%s] Cannot save highlights — Insight not found", task_id)
            return

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
    logger.info("[%s] Progressive save: %d highlights with snippets", task_id, len(resolved_highlights))


# ── Main Pipeline ───────────────────────────────────────────────────

@celery_app.task(bind=True, name="process_audio")
def process_audio_task(self, task_id: str, file_path: str):
    """
    Main audio processing pipeline with progressive saves.

    Each major stage saves its results to the database immediately,
    so the user can see partial results while processing continues.
    If a timeout or error occurs, all previously saved stages are preserved.
    """
    settings = get_settings()
    pipeline_start = time.time()

    try:
        # ── Step 1: Convert to WAV ──────────────────────────────────
        t0 = time.time()
        orig_size = os.path.getsize(file_path) / (1024 * 1024) if os.path.exists(file_path) else 0
        _log_sys_stats(task_id, "Audio Conversion Start", f"Input file size: {orig_size:.2f} MB")
        _update_task_status(task_id, "converting", 5.0)

        from app.services.audio_engine import convert_to_wav
        wav_path = convert_to_wav(file_path)
        wav_size = os.path.getsize(wav_path) / (1024 * 1024) if os.path.exists(wav_path) else 0
        _log_sys_stats(task_id, "Audio Conversion End", f"WAV: {wav_size:.2f} MB in {time.time()-t0:.1f}s")

        # ── Step 2: Transcribe ──────────────────────────────────────
        t0 = time.time()
        _log_sys_stats(task_id, "Transcription Start", "Loading Whisper Model...")
        _update_task_status(task_id, "transcribing", 15.0)

        from app.services.transcription import get_transcription_service
        stt = get_transcription_service()
        result = stt.transcribe(wav_path)
        _log_sys_stats(task_id, "Transcription End",
                       f"Duration: {result.duration:.1f}s | Words: {len(result.words)} "
                       f"| Lang: {result.language} | Took: {time.time()-t0:.1f}s")

        # 🔵 PROGRESSIVE SAVE: transcript + duration + audio
        # Upload WAV to storage first
        from app.services.storage import get_storage_service
        storage = get_storage_service()

        audio_filename = f"{task_id}.wav"
        audio_storage_path = os.path.join(settings.RAW_AUDIO_DIR, audio_filename)
        shutil.copy2(wav_path, audio_storage_path)
        dest_name = f"raw/{audio_filename}"
        audio_url = storage.upload_file(audio_storage_path, dest_name, "audio/wav")

        # Clean up local raw audio if S3 is enabled
        if storage.is_s3_enabled:
            try:
                os.remove(audio_storage_path)
            except OSError:
                pass

        _save_transcript_to_db(task_id, result.text, result.duration, audio_url)
        # → User can now see the transcript in the UI

        # ── Step 3: Filter fillers ──────────────────────────────────
        t0 = time.time()
        _log_sys_stats(task_id, "Filtering Start", "Identifying filler words...")

        from app.services.filter_engine import FillerFilterEngine
        filler_engine = FillerFilterEngine()
        filtered = filler_engine.filter(result.words)
        _log_sys_stats(task_id, "Filtering End",
                       f"Cleaned: {filtered.original_word_count} → {filtered.clean_word_count} words "
                       f"| Saved: {filtered.token_savings_percent:.1f}% tokens | Took: {time.time()-t0:.1f}s")

        # 🔵 PROGRESSIVE SAVE: clean transcript + token savings
        _save_filtered_to_db(task_id, filtered)

        # ── Step 4: LLM Insight Extraction ──────────────────────────
        t0 = time.time()
        _log_sys_stats(task_id, "LLM Extraction Start", "Sending to LLM...")
        _update_task_status(task_id, "analyzing", 45.0)

        from app.services.llm.base import get_llm_provider
        llm = get_llm_provider()
        insight_result = _run_async(llm.extract_insights(filtered.clean_text))
        _log_sys_stats(task_id, "LLM Extraction End",
                       f"Title: '{insight_result.title}' | Highlights: {len(insight_result.highlights)} "
                       f"| Took: {time.time()-t0:.1f}s")

        # 🔵 PROGRESSIVE SAVE: title + summary + mood + tags
        _save_insights_to_db(task_id, insight_result)
        # → User can now see title, summary, mood, tags in the UI

        # ── Step 5: Fuzzy match timestamps ──────────────────────────
        t0 = time.time()
        _log_sys_stats(task_id, "Timestamp Align Start", "Fuzzy matching highlights back to audio...")
        _update_task_status(task_id, "analyzing", 65.0)

        from app.services.fuzzy_matcher import resolve_all_highlights
        resolved_highlights = resolve_all_highlights(insight_result.highlights, result.words)
        _log_sys_stats(task_id, "Timestamp Align End",
                       f"Aligned {len(resolved_highlights)} highlights in {time.time()-t0:.1f}s")

        # ── Step 6: Slice audio snippets ────────────────────────────
        t0 = time.time()
        _log_sys_stats(task_id, "Audio Slicing Start", f"Slicing {len(resolved_highlights)} MP3 snippets...")
        _update_task_status(task_id, "slicing", 75.0)

        from app.services.audio_engine import create_highlight_snippets
        
        snippet_results = create_highlight_snippets(
            wav_path=wav_path,
            highlights=resolved_highlights,
            snippets_dir=settings.SNIPPETS_DIR,
            insight_id=task_id,
        )

        # Upload snippets to S3 and map URLs to highlights
        snippet_map = {}
        for s in snippet_results:
            dest_name = f"snippets/{os.path.basename(s.snippet_path)}"
            s3_url = storage.upload_file(s.snippet_path, dest_name, "audio/mpeg")
            snippet_map[s.highlight_id] = s3_url
            
            # Clean up local snippet after upload if S3 is enabled
            if storage.is_s3_enabled:
                try:
                    os.remove(s.snippet_path)
                except OSError:
                    pass
                    
        for h in resolved_highlights:
            h["snippet_url"] = snippet_map.get(h["id"])

        _log_sys_stats(task_id, "Audio Slicing End",
                       f"Snippets uploaded in {time.time()-t0:.1f}s")

        # ── Step 7: Pinecone Embedding + Upsert ─────────────────────
        t0 = time.time()
        _log_sys_stats(task_id, "Vector Embed Start", "Preparing chunks for Pinecone...")
        _update_task_status(task_id, "indexing", 85.0)

        _embed_and_upsert(task_id, result.text, insight_result)
        _log_sys_stats(task_id, "Vector Embed End",
                       f"Embeddings upserted in {time.time()-t0:.1f}s")

        # 🔵 FINAL PROGRESSIVE SAVE: highlights with snippet URLs
        _save_highlights_to_db(task_id, resolved_highlights)

        # ── Cleanup temp files ──────────────────────────────────────
        for f in [file_path, wav_path]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass

        _update_task_status(task_id, "completed", 100.0)
        total_elapsed = time.time() - pipeline_start
        logger.info("[%s] ✅ Pipeline complete in %.1fs (%.1f× realtime for %.1fs audio)",
                    task_id, total_elapsed, result.duration / total_elapsed if total_elapsed > 0 else 0,
                    result.duration)

    except SoftTimeLimitExceeded:
        total_elapsed = time.time() - pipeline_start
        logger.warning(
            "[%s] ⏰ Soft time limit reached after %.1fs — partial results preserved",
            task_id, total_elapsed,
        )
        _update_task_status(
            task_id, "partial", 0.0,
            error_message=f"Processing time limit reached after {total_elapsed:.0f}s. "
                          "Partial results (transcript and any completed analysis) are saved.",
        )
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
