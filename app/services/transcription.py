"""
VoiceInsight AI — Transcription Service (Faster-Whisper, Singleton)

Loads the Whisper model ONCE inside the Celery worker process and keeps it
in memory for all subsequent tasks (Architectural Guardrail B1).
"""

import logging
import time
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("voiceinsight.transcription")


@dataclass
class WordTimestamp:
    """A single word with its start/end time in the audio."""
    word: str
    start: float
    end: float


@dataclass
class TranscriptionResult:
    """Full transcription output from Whisper."""
    text: str
    words: list[WordTimestamp]
    language: str
    duration: float  # Audio duration in seconds


class TranscriptionService:
    """
    Singleton Whisper transcription service.

    The model is loaded lazily on first call and stays resident in memory.
    This avoids the ~30s cold-start per task that would happen if we
    loaded the model fresh every time.
    """

    _instance: "TranscriptionService | None" = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_model(self) -> None:
        """Load the Whisper model if not already loaded."""
        if self._model is not None:
            return
        self._load_model()

    def _load_model(self):
        """Lazy load the Whisper model into memory as a singleton."""
        if self._model is None:
            import time
            import psutil
            import os
            
            from app.core.config import get_settings
            settings = get_settings()
            
            process = psutil.Process(os.getpid())
            mem_before = process.memory_info().rss / (1024 * 1024)
            
            logger.info(
                "Loading Whisper model: %s (device=%s, compute_type=%s) [ProcRAM before: %.1f MB]", 
                settings.WHISPER_MODEL, settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE, mem_before
            )
            
            start_load = time.time()
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                settings.WHISPER_MODEL,
                device=settings.WHISPER_DEVICE,
                compute_type=settings.WHISPER_COMPUTE_TYPE,
            )
            
            mem_after = process.memory_info().rss / (1024 * 1024)
            logger.info(
                "Whisper model loaded successfully in %.1fs. [ProcRAM after: %.1f MB, Delta: +%.1f MB]", 
                time.time() - start_load, mem_after, mem_after - mem_before
            )

    def transcribe(self, audio_path: str | Path) -> TranscriptionResult:
        """
        Transcribe an audio file and return word-level timestamps.

        Uses distil-large-v3 optimizations:
        - beam_size=1 (greedy decoding, ~2× faster)
        - vad_filter=True (skip silence, major speedup for long recordings)
        - condition_on_previous_text=False (recommended for distil models)
        - language from config (skip auto-detect pass)

        Args:
            audio_path: Path to a WAV/MP3/M4A file.

        Returns:
            TranscriptionResult with full text, word timestamps, language, and duration.
        """
        self._ensure_model()
        audio_path = str(audio_path)

        from app.core.config import get_settings
        settings = get_settings()

        logger.info("Transcribing: %s (beam=%d, lang=%s, vad=on)",
                     os.path.basename(audio_path),
                     settings.WHISPER_BEAM_SIZE,
                     settings.WHISPER_LANGUAGE)

        t0 = time.time()
        segments, info = self._model.transcribe(
            audio_path,
            word_timestamps=True,
            beam_size=settings.WHISPER_BEAM_SIZE,
            language=settings.WHISPER_LANGUAGE if settings.WHISPER_LANGUAGE else None,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            condition_on_previous_text=False,
        )

        words: list[WordTimestamp] = []
        full_text_parts: list[str] = []

        # Eagerly collect all segments (the generator does actual inference)
        for segment in segments:
            full_text_parts.append(segment.text)
            if segment.words:
                for w in segment.words:
                    words.append(WordTimestamp(
                        word=w.word.strip(),
                        start=round(w.start, 3),
                        end=round(w.end, 3),
                    ))

        full_text = " ".join(full_text_parts).strip()
        elapsed = time.time() - t0

        logger.info(
            "Transcription complete in %.1fs: %d words, language=%s, duration=%.1fs (%.1f× realtime)",
            elapsed, len(words), info.language, info.duration,
            info.duration / elapsed if elapsed > 0 else 0,
        )

        return TranscriptionResult(
            text=full_text,
            words=words,
            language=info.language,
            duration=info.duration,
        )


def get_transcription_service() -> TranscriptionService:
    """Get the singleton transcription service."""
    return TranscriptionService()
