"""
VoiceInsight AI — LLM Provider Abstract Base Class

Defines the interface that all LLM providers (Gemini, Groq, OpenAI, DeepSeek)
must implement. Selection is controlled by the LLM_PROVIDER env var.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class InsightResult:
    """Structured output from insight extraction."""
    title: str
    summary: str
    mood: str                         # calm | energetic | reflective
    tags: list[str]
    highlights: list[dict]            # Each: {text, tag, startTime, endTime}
    raw_response: str = ""            # Raw LLM output for debugging
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMProvider(ABC):
    """
    Abstract interface for language model providers.

    To add a new provider:
    1. Create a new file in app/services/llm/ (e.g., deepseek.py)
    2. Implement this interface
    3. Register it in get_llm_provider()
    """

    @abstractmethod
    async def extract_insights(self, transcript: str) -> InsightResult:
        """
        Analyze a transcript and extract structured insights.

        Args:
            transcript: The cleaned transcript text (fillers already removed).

        Returns:
            InsightResult with title, summary, mood, tags, and 3 highlights.
        """
        ...

    @abstractmethod
    async def answer_query(self, query: str, context: str) -> str:
        """
        Answer a user question using retrieved context from RAG.

        Args:
            query: The user's question.
            context: Relevant transcript excerpts from Pinecone.

        Returns:
            A concise, direct answer string.
        """
        ...


def get_llm_provider() -> LLMProvider:
    """
    Factory function — returns the configured LLM provider.
    Controlled by the LLM_PROVIDER environment variable.
    """
    from app.core.config import get_settings
    settings = get_settings()

    provider = settings.LLM_PROVIDER.lower()

    if provider == "gemini":
        from app.services.llm.gemini import GeminiProvider
        return GeminiProvider()
    elif provider == "litert":
        from app.services.llm.litert import LiteRTProvider
        return LiteRTProvider()
    elif provider == "groq":
        from app.services.llm.groq import GroqProvider
        return GroqProvider()
    elif provider == "openai":
        from app.services.llm.openai import OpenAIProvider
        return OpenAIProvider()
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER: '{provider}'. "
            f"Supported: gemini, litert, groq, openai"
        )
