"""
VoiceInsight AI — Fuzzy Phrase Matcher (RapidFuzz)

Resolves LLM-returned highlight quotes back to precise timestamps in the
original transcript, even when the LLM slightly alters the text.

This replaces the crude 50%-word-match logic from the old codebase.
"""

import logging
from dataclasses import dataclass

from rapidfuzz import fuzz
from app.services.transcription import WordTimestamp

logger = logging.getLogger("voiceinsight.fuzzy_matcher")


@dataclass
class MatchResult:
    """Result of matching a quote to timestamps."""
    quote: str
    matched_text: str
    start_time: float
    end_time: float
    confidence: float  # 0-100


def find_quote_timestamps(
    quote: str,
    word_timestamps: list[WordTimestamp],
    min_confidence: float = 60.0,
) -> MatchResult | None:
    """
    Find the best matching position for a quote in the timestamped word list.

    Uses a sliding-window approach with RapidFuzz partial_ratio scoring
    to handle LLM-altered quotes (grammar fixes, filler removal, etc.)

    Args:
        quote: The text quote returned by the LLM.
        word_timestamps: Full list of word timestamps from Whisper.
        min_confidence: Minimum fuzzy match score (0-100) to accept.

    Returns:
        MatchResult with precise timestamps, or None if no good match found.
    """
    if not quote or not word_timestamps:
        return None

    quote_words = quote.lower().split()
    quote_len = len(quote_words)

    if quote_len == 0:
        return None

    best_score = 0.0
    best_start_idx = 0
    best_end_idx = 0
    best_window_text = ""

    # Sliding window: try different window sizes around the quote length
    # to account for LLM adding/removing words
    for window_size in range(max(1, quote_len - 3), quote_len + 5):
        if window_size > len(word_timestamps):
            continue

        for i in range(len(word_timestamps) - window_size + 1):
            window_words = [wt.word.lower().strip() for wt in word_timestamps[i:i + window_size]]
            window_text = " ".join(window_words)

            score = fuzz.partial_ratio(quote.lower(), window_text)

            if score > best_score:
                best_score = score
                best_start_idx = i
                best_end_idx = i + window_size - 1
                best_window_text = window_text

            # Early exit on perfect match
            if score >= 98.0:
                break
        if best_score >= 98.0:
            break

    if best_score < min_confidence:
        logger.warning(
            "Fuzzy match below threshold (%.1f < %.1f): '%s...'",
            best_score, min_confidence, quote[:60],
        )
        return None

    start_time = word_timestamps[best_start_idx].start
    end_time = word_timestamps[best_end_idx].end

    logger.debug(
        "Fuzzy match (%.1f%%): '%s...' → %.2fs-%.2fs",
        best_score, quote[:40], start_time, end_time,
    )

    return MatchResult(
        quote=quote,
        matched_text=best_window_text,
        start_time=start_time,
        end_time=end_time,
        confidence=best_score,
    )


def resolve_all_highlights(
    highlights: list[dict],
    word_timestamps: list[WordTimestamp],
) -> list[dict]:
    """
    Resolve timestamps for all highlights using fuzzy matching.

    Args:
        highlights: List of dicts with 'text', 'tag', etc. from LLM.
        word_timestamps: Full word timestamps from Whisper.

    Returns:
        Updated highlights list with resolved start_time and end_time.
    """
    resolved = []

    for i, h in enumerate(highlights):
        quote = h.get("text", "")
        match = find_quote_timestamps(quote, word_timestamps)

        resolved_h = {
            "id": h.get("id", f"h{i + 1}"),
            "text": h.get("text", ""),
            "tag": h.get("tag", "#Realization"),
        }

        if match:
            resolved_h["start_time"] = match.start_time
            resolved_h["end_time"] = match.end_time
            resolved_h["confidence"] = match.confidence
            logger.info(
                "Highlight %s resolved: %.2fs → %.2fs (confidence: %.1f%%)",
                resolved_h["id"], match.start_time, match.end_time, match.confidence,
            )
        else:
            # Fallback: use the LLM-provided times or defaults
            resolved_h["start_time"] = h.get("startTime", h.get("start_time", 0.0))
            resolved_h["end_time"] = h.get("endTime", h.get("end_time", 0.0))
            resolved_h["confidence"] = 0.0
            logger.warning("Highlight %s: using LLM-provided timestamps (no fuzzy match)", resolved_h["id"])

        resolved.append(resolved_h)

    return resolved
