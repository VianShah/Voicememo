"""
VoiceInsight AI — LiteRT (Google AI Edge) LLM Provider

Runs LLMs (like Gemma) locally using the LiteRT-LM runtime.
Requires a .litertlm model file.
"""

import json
import logging
import re
import asyncio
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
        # Defaults to CPU. Change to GPU if supported and preferred.
        _ENGINE = litert_lm.Engine(str(model_path))
    
    return _ENGINE


class LiteRTProvider(LLMProvider):
    """Local LLM implementation using Google AI Edge LiteRT-LM."""

    def __init__(self):
        if not HAS_LITERT:
            raise ImportError("litert-lm-api is not installed")
        
        # Trigger engine load
        try:
            self._engine = get_engine()
        except Exception as e:
            logger.error("Failed to initialize LiteRT engine: %s", e)
            raise

    async def _generate(self, prompt: str) -> str:
        """Helper to run inference in the engine."""
        # LiteRT-LM calls are generally synchronous in their current Python bindings,
        # so we run in a thread to avoid blocking the event loop.
        def _run():
            with self._engine.create_conversation() as conv:
                response = conv.send_message(prompt)
                # The response structure contains a list of content parts
                # Based on litert-lm-api docs: response["content"][0]["text"]
                return response["content"][0]["text"]

        return await asyncio.to_thread(_run)

    async def extract_insights(self, transcript: str) -> InsightResult:
        """
        Analyze a transcript locally using LiteRT-LM.
        """
        prompt = f"""You are an expert insight extractor for voice memos.
Analyze this transcript carefully.
Return ONLY valid JSON with this structure:
{{
  "title": "a short title",
  "summary": "2-3 sentence summary",
  "mood": "calm|energetic|reflective",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "highlights": [
    {{
      "id": "h1",
      "text": "verbatim quote from transcript",
      "tag": "#Realization|#ActionItem|#Memory",
      "startTime": 0,
      "endTime": 15
    }}
  ]
}}

TRANSCRIPT:
{transcript}"""

        logger.info("Running local LiteRT inference for insight extraction")
        
        try:
            raw = await self._generate(prompt)
            raw = raw.strip()

            # Clean JSON
            raw = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"^```\s*", "", raw)
            raw = re.sub(r"```\s*$", "", raw)
            raw = raw.strip()

            parsed = json.loads(raw)
        except Exception as e:
            logger.error("LiteRT inference or parsing failed: %s", e)
            # Fallback or re-raise
            raise ValueError(f"LiteRT local processing failed: {e}")

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
        prompt = f"""Answer the user's question using ONLY the context provided below.
If not in the context, say you don't know. Be concise.

CONTEXT:
{context}

QUESTION: {query}"""

        logger.info("Running local LiteRT inference for RAG query")
        return await self._generate(prompt)
