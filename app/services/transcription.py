"""
VoiceInsight AI — Transcription Service (Faster-Whisper, Singleton)

Loads the Whisper model ONCE inside the Celery worker process and keeps it
in memory for all subsequent tasks (Architectural Guardrail B1).
"""

import logging
import tempfile
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

        from faster_whisper import WhisperModel
        from app.core.config import get_settings

        settings = get_settings()
        logger.info(
            "Loading Whisper model: %s (device=%s, compute_type=%s)...",
            settings.WHISPER_MODEL,
            settings.WHISPER_DEVICE,
            settings.WHISPER_COMPUTE_TYPE,
        )
        self._model = WhisperModel(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
        )
        logger.info("Whisper model loaded successfully.")

    def transcribe(self, audio_path: str | Path) -> TranscriptionResult:
        """
        Transcribe an audio file and return word-level timestamps.

        Args:
            audio_path: Path to a WAV/MP3/M4A file.

        Returns:
            TranscriptionResult with full text, word timestamps, language, and duration.
        """
        self._ensure_model()
        audio_path = str(audio_path)

        logger.info("Transcribing: %s", os.path.basename(audio_path))
        segments, info = self._model.transcribe(
            audio_path,
            word_timestamps=True,
            language=None,  # Auto-detect
        )

        words: list[WordTimestamp] = []
        full_text_parts: list[str] = []

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
        logger.info(
            "Transcription complete: %d words, language=%s, duration=%.1fs",
            len(words), info.language, info.duration,
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
