"""
VoiceInsight AI — LiteRT (Google AI Edge) LLM Provider

Runs LLMs (like Gemma) locally using the LiteRT-LM runtime.
Requires a .litertlm model file.

Optimizations applied:
- Smart transcript truncation (3000 char cap, sentence-boundary aware)
- Simplified prompt for 2B model
- 300-second generous timeout with graceful fallback
- Minimal fallback InsightResult on timeout (preserves transcript)
"""

import json
import logging
import re
import asyncio
import threading
import time
from pathlib import Path

try:
    import litert_lm
    HAS_LITERT = True
except ImportError:
    HAS_LITERT = False

from app.core.config import get_settings
from app.services.llm.base import LLMProvider, InsightResult

logger = logging.getLogger("voiceinsight.llm.litert")

# ── Global Engine Singleton ─────────────────────────────────────────
_ENGINE = None

def get_engine():
    """Get or initialize the LiteRT-LM engine."""
    global _ENGINE
    if _ENGINE is None:
        if not HAS_LITERT:
            raise ImportError("litert-lm-api is not installed. Add it to requirements.txt")
        
        settings = get_settings()
        model_path = Path(settings.LITERT_MODEL_PATH)
        
        if not model_path.exists():
            raise FileNotFoundError(f"LiteRT model not found at {model_path}. Please download a .litertlm model.")
        
        logger.info("Initializing LiteRT-LM Engine with model: %s", model_path)
        t0 = time.time()
        import psutil
        import os
        process = psutil.Process(os.getpid())
        mem_before = process.memory_info().rss / (1024 * 1024)

        # Defaults to CPU. Change to GPU if supported and preferred.
        _ENGINE = litert_lm.Engine(str(model_path))
        
        mem_after = process.memory_info().rss / (1024 * 1024)
        elapsed = time.time() - t0
        logger.info(
            "LiteRT engine loaded in %.1fs [ProcRAM: %.1f MB → %.1f MB, Delta: +%.1f MB]", 
            elapsed, mem_before, mem_after, mem_after - mem_before
        )
    
    return _ENGINE


# ── Transcript Truncation ───────────────────────────────────────────
def _truncate_transcript(transcript: str, max_chars: int = 3000) -> str:
    """
    Truncate long transcripts while preserving meaning.

    For 45-min recordings, the transcript can be 10,000+ chars — far too
    much for a 2B model to process in reasonable time. This preserves the
    beginning (context setup), end (conclusions), and evenly sampled
    middle sections. Sentence-boundary aware to avoid cutting mid-thought.

    Args:
        transcript: Full transcript text.
        max_chars: Maximum character limit.

    Returns:
        Truncated transcript, or original if already under the limit.
    """
    if len(transcript) <= max_chars:
        return transcript

    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', transcript)
    if len(sentences) <= 3:
        # Very few sentences — just hard-truncate
        return transcript[:max_chars] + "..."

    # Allocate ~40% to head, ~30% to middle, ~30% to tail
    head_budget = int(max_chars * 0.4)
    mid_budget = int(max_chars * 0.3)
    tail_budget = int(max_chars * 0.3)

    # Build head (from beginning)
    head_parts: list[str] = []
    head_count = 0
    for s in sentences:
        if head_count + len(s) + 1 > head_budget:
            break
        head_parts.append(s)
        head_count += len(s) + 1

    # Build tail (from end, reversed)
    tail_parts: list[str] = []
    tail_count = 0
    for s in reversed(sentences):
        if tail_count + len(s) + 1 > tail_budget:
            break
        tail_parts.insert(0, s)
        tail_count += len(s) + 1

    # Build middle (sample evenly from remaining sentences)
    used_head = len(head_parts)
    used_tail = len(tail_parts)
    middle_candidates = sentences[used_head:len(sentences) - used_tail if used_tail else len(sentences)]

    mid_parts: list[str] = []
    mid_count = 0
    if middle_candidates:
        step = max(1, len(middle_candidates) // 5)  # Sample ~5 sentences
        for i in range(0, len(middle_candidates), step):
            s = middle_candidates[i]
            if mid_count + len(s) + 1 > mid_budget:
                break
            mid_parts.append(s)
            mid_count += len(s) + 1

    head_text = " ".join(head_parts)
    mid_text = " ".join(mid_parts)
    tail_text = " ".join(tail_parts)

    truncated = f"{head_text}\n[...]\n{mid_text}\n[...]\n{tail_text}"

    logger.info(
        "Transcript truncated: %d → %d chars (%.0f%% reduction)",
        len(transcript), len(truncated),
        (1 - len(truncated) / len(transcript)) * 100,
    )
    return truncated


class LiteRTProvider(LLMProvider):
    """Local LLM implementation using Google AI Edge LiteRT-LM."""

    # Generous timeout for CPU inference (5 minutes)
    INFERENCE_TIMEOUT = 300

    def __init__(self):
        if not HAS_LITERT:
            raise ImportError("litert-lm-api is not installed")
        
        # Trigger engine load
        try:
            self._engine = get_engine()
        except Exception as e:
            logger.error("Failed to initialize LiteRT engine: %s", e)
            raise

    async def _generate(self, prompt: str, timeout: int | None = None) -> str:
        """
        Run inference with a generous timeout.

        Args:
            prompt: The prompt to send to the model.
            timeout: Max seconds to wait. Defaults to INFERENCE_TIMEOUT (300s).

        Returns:
            Model response text, or None on timeout.
        """
        timeout = timeout or self.INFERENCE_TIMEOUT
        result_holder: list = [None]
        error_holder: list = [None]

        def _run():
            try:
                with self._engine.create_conversation() as conv:
                    response = conv.send_message(prompt)
                    # The response structure contains a list of content parts
                    # Based on litert-lm-api docs: response["content"][0]["text"]
                    result_holder[0] = response["content"][0]["text"]
            except Exception as e:
                error_holder[0] = e

        t0 = time.time()
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        # Wait with generous timeout
        await asyncio.to_thread(thread.join, timeout)
        elapsed = time.time() - t0

        if thread.is_alive():
            logger.warning(
                "LiteRT inference exceeded %ds timeout (ran for %.1fs). "
                "Returning None — caller should use fallback.",
                timeout, elapsed,
            )
            return None

        if error_holder[0]:
            raise error_holder[0]

        import psutil
        import os
        process = psutil.Process(os.getpid())
        mem_after = process.memory_info().rss / (1024 * 1024)
        
        logger.info("LiteRT inference completed in %.1fs [ProcRAM after: %.1f MB]", elapsed, mem_after)
        return result_holder[0]

    def _fallback_result(self) -> InsightResult:
        """
        Minimal fallback InsightResult when LiteRT times out.

        The transcript is still preserved — only the AI analysis is missing.
        The user sees the transcript and a message that analysis timed out.
        """
        return InsightResult(
            title="Voice Memo",
            summary="AI analysis timed out. Your full transcript is preserved below.",
            mood="calm",
            tags=["voice-memo"],
            highlights=[],
            raw_response="",
        )

    async def extract_insights(self, transcript: str) -> InsightResult:
        """
        Analyze a transcript locally using LiteRT-LM.

        Applies transcript truncation for long recordings and uses a
        simplified prompt optimized for the 2B model.
        """
        # Truncate long transcripts to stay within model capacity
        truncated = _truncate_transcript(transcript)

        # Simplified prompt — shorter = faster for 2B model
        prompt = f"""Analyze this voice memo transcript. Return ONLY valid JSON:
{{
  "title": "short descriptive title",
  "summary": "2-3 sentence summary",
  "mood": "calm|energetic|reflective",
  "tags": ["tag1", "tag2", "tag3"],
  "highlights": [
    {{
      "id": "h1",
      "text": "exact quote from transcript",
      "tag": "#Realization|#ActionItem|#Memory"
    }}
  ]
}}

TRANSCRIPT:
{truncated}"""

        logger.info(
            "Running LiteRT inference (transcript: %d chars, prompt: %d chars, timeout: %ds)",
            len(truncated), len(prompt), self.INFERENCE_TIMEOUT,
        )
        
        try:
            raw = await self._generate(prompt)

            # Handle timeout (None return)
            if raw is None:
                logger.warning("LiteRT timed out — returning fallback result")
                return self._fallback_result()

            raw = raw.strip()

            # Clean JSON
            raw = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"^```\s*", "", raw)
            raw = re.sub(r"```\s*$", "", raw)
            raw = raw.strip()

            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("LiteRT returned invalid JSON: %s (raw: %.200s...)", e, raw)
            return self._fallback_result()
        except Exception as e:
            logger.error("LiteRT inference failed: %s", e)
            return self._fallback_result()

        highlights = parsed.get("highlights", [])
        for i, h in enumerate(highlights):
            if "id" not in h:
                h["id"] = f"h{i + 1}"

        return InsightResult(
            title=parsed.get("title", "Untitled"),
            summary=parsed.get("summary", ""),
            mood=parsed.get("mood", "calm"),
            tags=parsed.get("tags", []),
            highlights=highlights,
            raw_response=raw,
        )

    async def answer_query(self, query: str, context: str) -> str:
        """
        Answer a user question locally using LiteRT-LM.
        """
        # Truncate context for RAG queries too
        context = _truncate_transcript(context, max_chars=2000)

        prompt = f"""Answer the user's question using ONLY the context provided below.
If not in the context, say you don't know. Be concise.

CONTEXT:
{context}

QUESTION: {query}"""

        logger.info("Running local LiteRT inference for RAG query")
        result = await self._generate(prompt, timeout=120)  # Shorter timeout for queries
        if result is None:
            return "I'm sorry, the local AI took too long to process your question. Please try again."
        return result
