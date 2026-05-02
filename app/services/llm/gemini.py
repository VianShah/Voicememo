"""
VoiceInsight AI — Gemini LLM Provider (gemini-2.5-flash via google-genai SDK)

Primary LLM provider for insight extraction and RAG queries.
Uses the new google-genai SDK (the old google-generativeai is EOL).
"""

import json
import logging
import re

from google import genai

from app.core.config import get_settings
from app.services.llm.base import LLMProvider, InsightResult

logger = logging.getLogger("voiceinsight.llm.gemini")


class GeminiProvider(LLMProvider):
    """Google Gemini 2.5 Flash implementation of LLMProvider."""

    def __init__(self):
        settings = get_settings()
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set")

        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model = settings.GEMINI_MODEL
        logger.info("GeminiProvider initialized (model=%s)", self._model)

    async def extract_insights(self, transcript: str) -> InsightResult:
        """
        Analyze a transcript and extract structured insights using Gemini 2.5 Flash.
        """
        prompt = f"""You are an expert insight extractor for voice memos.
Analyze this transcript carefully.
Return ONLY valid JSON (no markdown, no backticks) with this structure:
{{
  "title": "a short, catchy title (max 8 words)",
  "summary": "2-3 sentence summary of what was discussed",
  "mood": "calm|energetic|reflective",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "highlights": [
    {{
      "id": "h1",
      "text": "verbatim quote that was impactful",
      "tag": "#Realization|#ActionItem|#Memory",
      "startTime": 0,
      "endTime": 15
    }}
  ]
}}

CRITICAL RULES:
1. The "text" in highlights MUST be a verbatim substring of the transcript.
2. Select the 3 most impactful segments as highlights.
3. Each highlight should be approximately 10-20 seconds of speech.
4. Return ONLY the JSON object. No commentary.

TRANSCRIPT:
{transcript}"""

        logger.info("Calling Gemini %s for insight extraction (%d chars)", self._model, len(transcript))

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
        )

        raw = response.text.strip()

        # Strip any accidental markdown fences
        raw = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"^```\s*", "", raw)
        raw = re.sub(r"```\s*$", "", raw)
        raw = raw.strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Gemini returned malformed JSON: %s", raw[:300])
            raise ValueError(f"Gemini returned malformed JSON: {e}")

        # Ensure highlights have IDs
        highlights = parsed.get("highlights", [])
        for i, h in enumerate(highlights):
            if "id" not in h:
                h["id"] = f"h{i + 1}"

        # Extract token usage
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        completion_tokens = getattr(usage, "candidates_token_count", 0) or 0

        logger.info(
            "Gemini extraction complete: title='%s', highlights=%d, tokens=%d",
            parsed.get("title", "?"), len(highlights), prompt_tokens + completion_tokens,
        )

        return InsightResult(
            title=parsed.get("title", "Untitled"),
            summary=parsed.get("summary", ""),
            mood=parsed.get("mood", "calm"),
            tags=parsed.get("tags", []),
            highlights=highlights,
            raw_response=raw,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

    async def answer_query(self, query: str, context: str) -> str:
        """
        Answer a user question using retrieved RAG context.
        """
        prompt = f"""You are a personal AI assistant for "The Insight Recorder" app.
Answer the user's question using ONLY the transcript excerpts provided below.
If the answer isn't in the excerpts, say you don't have that in their recordings.
Be concise and direct.

CONTEXT FROM RECORDINGS:
{context}

USER QUESTION: {query}"""

        logger.info("Calling Gemini %s for RAG query", self._model)

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
        )

        answer = response.text.strip()
        logger.info("RAG answer generated (%d chars)", len(answer))
        return answer
