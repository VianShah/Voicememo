"""
VoiceInsight AI — Audio Engine (FFmpeg conversion + pydub snippet slicing)

Handles:
- WebM/MP3/M4A → WAV conversion via FFmpeg
- Slicing highlight snippets into separate MP3 files
"""

import logging
import os
import subprocess
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger("voiceinsight.audio_engine")


@dataclass
class SnippetResult:
    """Result of slicing a single audio snippet."""
    highlight_id: str
    snippet_path: str
    snippet_url: str
    start_time: float
    end_time: float
    duration: float


def convert_to_wav(input_path: str | Path, output_path: str | Path | None = None) -> str:
    """
    Convert any audio file to 16kHz mono WAV using FFmpeg.

    Args:
        input_path: Path to the source audio file (WebM, MP3, M4A, etc.)
        output_path: Optional output path. Defaults to same location with .wav extension.

    Returns:
        Path to the converted WAV file.
    """
    input_path = str(input_path)
    if output_path is None:
        output_path = os.path.splitext(input_path)[0] + ".wav"
    output_path = str(output_path)

    logger.info("Converting %s → %s", os.path.basename(input_path), os.path.basename(output_path))

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ac", "1",          # Mono
        "-ar", "16000",      # 16kHz — standard for speech recognition
        "-f", "wav",
        output_path,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        logger.error("FFmpeg failed: %s", result.stderr[:500])
        raise RuntimeError(f"FFmpeg conversion failed: {result.stderr[:300]}")

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    logger.info("Conversion complete: %.2f MB", size_mb)
    return output_path


def slice_snippet(
    wav_path: str | Path,
    start_time: float,
    end_time: float,
    output_path: str | Path,
) -> str:
    """
    Slice an audio segment from a WAV file and save as MP3.

    Uses pydub for precise millisecond-level slicing.

    Args:
        wav_path: Path to the source WAV file.
        start_time: Start time in seconds.
        end_time: End time in seconds.
        output_path: Path to save the output MP3 snippet.

    Returns:
        Path to the created MP3 snippet.
    """
    from pydub import AudioSegment

    wav_path = str(wav_path)
    output_path = str(output_path)

    logger.debug("Slicing snippet: %.2fs → %.2fs from %s", start_time, end_time, os.path.basename(wav_path))

    audio = AudioSegment.from_wav(wav_path)

    # pydub works in milliseconds
    start_ms = int(start_time * 1000)
    end_ms = int(end_time * 1000)

    # Add a small padding (200ms) around the snippet for natural sound
    start_ms = max(0, start_ms - 200)
    end_ms = min(len(audio), end_ms + 200)

    snippet = audio[start_ms:end_ms]

    # Apply fade-in/out for smooth playback
    snippet = snippet.fade_in(50).fade_out(50)

    snippet.export(output_path, format="mp3", bitrate="128k")

    duration = (end_ms - start_ms) / 1000
    logger.debug("Snippet saved: %s (%.1fs)", os.path.basename(output_path), duration)
    return output_path


def create_highlight_snippets(
    wav_path: str | Path,
    highlights: list[dict],
    snippets_dir: str | Path,
    insight_id: str,
    base_url: str = "/v1/snippets",
) -> list[SnippetResult]:
    """
    Create MP3 snippet files for each highlight.

    Args:
        wav_path: Path to the source WAV file.
        highlights: List of dicts with 'id', 'start_time', 'end_time'.
        snippets_dir: Directory to save snippet files.
        insight_id: Parent insight ID for naming.
        base_url: Base URL for serving snippets.

    Returns:
        List of SnippetResult objects with file paths and URLs.
    """
    snippets_dir = Path(snippets_dir)
    snippets_dir.mkdir(parents=True, exist_ok=True)
    results: list[SnippetResult] = []

    for h in highlights:
        h_id = h.get("id", "unknown")
        start = h.get("start_time", 0.0)
        end = h.get("end_time", 0.0)

        if end <= start:
            logger.warning("Skipping highlight %s: invalid time range (%.2f → %.2f)", h_id, start, end)
            continue

        filename = f"{insight_id}_{h_id}.mp3"
        output_path = str(snippets_dir / filename)
        snippet_url = f"{base_url}/{filename}"

        try:
            slice_snippet(wav_path, start, end, output_path)
            results.append(SnippetResult(
                highlight_id=h_id,
                snippet_path=output_path,
                snippet_url=snippet_url,
                start_time=start,
                end_time=end,
                duration=round(end - start, 2),
            ))
        except Exception as e:
            logger.error("Failed to slice snippet %s: %s", h_id, e)

    logger.info("Created %d/%d audio snippets for insight %s", len(results), len(highlights), insight_id)
    return results
