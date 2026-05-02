"""
VoiceInsight AI — Groq LLM Provider (Stub)

Ready-to-use provider for Groq's ultra-low-latency Llama models.
Will activate automatically when GROQ_API_KEY is set and LLM_PROVIDER=groq.
"""

import logging
from app.services.llm.base import LLMProvider, InsightResult

logger = logging.getLogger("voiceinsight.llm.groq")


class GroqProvider(LLMProvider):
    """Groq implementation of LLMProvider — currently a stub."""

    def __init__(self):
        from app.core.config import get_settings
        settings = get_settings()
        if not settings.GROQ_API_KEY:
            logger.warning("GroqProvider initialized but GROQ_API_KEY is not set")
        self._api_key = settings.GROQ_API_KEY

    async def extract_insights(self, transcript: str) -> InsightResult:
        if not self._api_key:
            raise NotImplementedError(
                "Groq provider is not configured. Set GROQ_API_KEY in .env"
            )
        # TODO: Implement using groq Python SDK
        raise NotImplementedError("Groq insight extraction not yet implemented")

    async def answer_query(self, query: str, context: str) -> str:
        if not self._api_key:
            raise NotImplementedError(
                "Groq provider is not configured. Set GROQ_API_KEY in .env"
            )
        # TODO: Implement using groq Python SDK
        raise NotImplementedError("Groq query answering not yet implemented")
