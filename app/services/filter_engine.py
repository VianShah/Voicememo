"""
VoiceInsight AI — Filler-Filter Engine

Removes filler words (um, uh, like, basically, etc.) from transcripts
to reduce LLM token costs, while maintaining a Coordinate Map that
links cleaned word positions back to original timestamped positions.
"""

import logging
import re
from dataclasses import dataclass, field

from app.services.transcription import WordTimestamp
from app.core.config import get_settings

logger = logging.getLogger("voiceinsight.filter_engine")


@dataclass
class CoordinateEntry:
    """Maps a word in the cleaned text back to its original position."""
    clean_index: int       # Position in the cleaned word list
    original_index: int    # Position in the original word list
    word: str
    start: float           # Original start time
    end: float             # Original end time


@dataclass
class FilteredTranscript:
    """Result of the filler-filter process."""
    clean_text: str
    coordinate_map: list[CoordinateEntry]
    original_word_count: int
    clean_word_count: int
    removed_count: int
    token_savings_percent: float


class FillerFilterEngine:
    """
    Pre-processor that strips filler words from a transcript while
    maintaining a Coordinate Map for timestamp resolution.
    """

    def __init__(self, filler_words: list[str] | None = None):
        if filler_words is None:
            filler_words = get_settings().filler_word_list

        # Sort multi-word fillers by length (longest first) so "you know"
        # is matched before individual "you" or "know"
        self._multi_word_fillers = sorted(
            [f for f in filler_words if " " in f],
            key=len,
            reverse=True,
        )
        self._single_word_fillers = set(
            f for f in filler_words if " " not in f
        )

    def filter(self, words: list[WordTimestamp]) -> FilteredTranscript:
        """
        Remove filler words and collapse stutters while building a coordinate map.

        Args:
            words: Word-level timestamps from the transcription service.

        Returns:
            FilteredTranscript with clean text and coordinate map.
        """
        if not words:
            return FilteredTranscript(
                clean_text="",
                coordinate_map=[],
                original_word_count=0,
                clean_word_count=0,
                removed_count=0,
                token_savings_percent=0.0,
            )

        # Collapse stuttered repetitions (3+ consecutive identical words)
        words = self._collapse_repetitions(words)

        original_count = len(words)

        # First pass: mark multi-word fillers
        skip_indices: set[int] = set()
        i = 0
        while i < len(words):
            for filler in self._multi_word_fillers:
                filler_parts = filler.split()
                filler_len = len(filler_parts)

                if i + filler_len <= len(words):
                    window = [
                        self._normalize(words[i + j].word)
                        for j in range(filler_len)
                    ]
                    if window == filler_parts:
                        for j in range(filler_len):
                            skip_indices.add(i + j)
                        i += filler_len
                        break
            else:
                i += 1

        # Second pass: mark single-word fillers and build coordinate map
        coordinate_map: list[CoordinateEntry] = []
        clean_words: list[str] = []
        clean_idx = 0

        for orig_idx, wt in enumerate(words):
            if orig_idx in skip_indices:
                continue

            normalized = self._normalize(wt.word)
            if normalized in self._single_word_fillers:
                continue

            coordinate_map.append(CoordinateEntry(
                clean_index=clean_idx,
                original_index=orig_idx,
                word=wt.word,
                start=wt.start,
                end=wt.end,
            ))
            clean_words.append(wt.word)
            clean_idx += 1

        clean_text = " ".join(clean_words)
        clean_count = len(clean_words)
        removed = original_count - clean_count
        savings = (removed / original_count * 100) if original_count > 0 else 0.0

        logger.info(
            "Filler filter: %d → %d words (removed %d, saved %.1f%% tokens)",
            original_count, clean_count, removed, savings,
        )

        return FilteredTranscript(
            clean_text=clean_text,
            coordinate_map=coordinate_map,
            original_word_count=original_count,
            clean_word_count=clean_count,
            removed_count=removed,
            token_savings_percent=round(savings, 1),
        )

    @staticmethod
    def _normalize(word: str) -> str:
        """Lowercase and strip punctuation for comparison."""
        return re.sub(r"[^\w\s]", "", word.lower()).strip()

    @staticmethod
    def _collapse_repetitions(
        words: list[WordTimestamp], min_repeat: int = 3
    ) -> list[WordTimestamp]:
        """
        Collapse 3+ consecutive identical words (stutters) into one.

        "the the the project" → "the project"
        "I I think" → kept as-is (only 2, could be emphasis)

        This is intentionally conservative — we never remove words that
        might carry semantic intent. Only clear stutters (3+) are collapsed.
        """
        if not words:
            return words

        result: list[WordTimestamp] = []
        i = 0
        collapsed_count = 0
        while i < len(words):
            count = 1
            normalized = words[i].word.lower().strip()
            while (
                i + count < len(words)
                and words[i + count].word.lower().strip() == normalized
            ):
                count += 1

            result.append(words[i])  # Always keep first occurrence
            if count >= min_repeat:
                collapsed_count += count - 1
                i += count  # Skip the repeated duplicates
            else:
                i += 1  # Keep all (not enough repeats to be a stutter)

        if collapsed_count > 0:
            logger.info(
                "Repetition collapse: removed %d stuttered words",
                collapsed_count,
            )
        return result
