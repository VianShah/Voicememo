"""
VoiceInsight AI — OpenAI LLM Provider (Stub)

Ready-to-use provider for OpenAI GPT-4o/o1 models.
Will activate automatically when OPENAI_API_KEY is set and LLM_PROVIDER=openai.
"""

import logging
from app.services.llm.base import LLMProvider, InsightResult

logger = logging.getLogger("voiceinsight.llm.openai")


class OpenAIProvider(LLMProvider):
    """OpenAI implementation of LLMProvider — currently a stub."""

    def __init__(self):
        from app.core.config import get_settings
        settings = get_settings()
        if not settings.OPENAI_API_KEY:
            logger.warning("OpenAIProvider initialized but OPENAI_API_KEY is not set")
        self._api_key = settings.OPENAI_API_KEY

    async def extract_insights(self, transcript: str) -> InsightResult:
        if not self._api_key:
            raise NotImplementedError(
                "OpenAI provider is not configured. Set OPENAI_API_KEY in .env"
            )
        # TODO: Implement using openai Python SDK
        raise NotImplementedError("OpenAI insight extraction not yet implemented")

    async def answer_query(self, query: str, context: str) -> str:
        if not self._api_key:
            raise NotImplementedError(
                "OpenAI provider is not configured. Set OPENAI_API_KEY in .env"
            )
        # TODO: Implement using openai Python SDK
        raise NotImplementedError("OpenAI query answering not yet implemented")
