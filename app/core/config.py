"""
VoiceInsight AI — Application Configuration
Reads all settings from environment variables / .env file.
"""

from pydantic_settings import BaseSettings
from pathlib import Path
from functools import lru_cache


class Settings(BaseSettings):
    """Central configuration loaded from environment variables."""

    # ── API Keys ────────────────────────────────────────────────────
    GEMINI_API_KEY: str = ""
    PINECONE_API_KEY: str = ""
    PINECONE_INDEX: str = "voicememos"

    # ── LLM Configuration ───────────────────────────────────────────
    LLM_PROVIDER: str = "gemini"          # gemini | groq | openai | deepseek | litert
    GEMINI_MODEL: str = "gemini-2.5-flash"
    LITERT_MODEL_PATH: str = "./models/gemma-2b-it.litertlm"
    GROQ_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""

    # ── Whisper Configuration ───────────────────────────────────────
    WHISPER_MODEL: str = "large-v3-turbo"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"

    # ── Database ────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://voiceinsight:voiceinsight@localhost:5432/voiceinsight"

    # ── Redis / Celery ──────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    STORAGE_DIR: str = "./data"
    RAW_AUDIO_DIR: str = "./data/raw"
    SNIPPETS_DIR: str = "./data/snippets"

    # ── S3 Object Storage ───────────────────────────────────────────
    S3_BUCKET_NAME: str = ""
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "auto"
    S3_ENDPOINT_URL: str = ""

    # ── Server ──────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    APP_URL: str = "http://localhost:8000"

    # ── Filler Filter ───────────────────────────────────────────────
    FILLER_WORDS: str = "um,uh,erm,ah,like,basically,you know,i mean,sort of,kind of,right,actually,literally,honestly,so yeah"

    # ── Embedding ───────────────────────────────────────────────────
    EMBEDDING_MODEL: str = "multilingual-e5-large"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }

    @property
    def filler_word_list(self) -> list[str]:
        """Parse comma-separated filler words into a list."""
        return [w.strip().lower() for w in self.FILLER_WORDS.split(",") if w.strip()]

    def ensure_dirs(self) -> None:
        """Create storage directories if they don't exist."""
        for d in [self.STORAGE_DIR, self.RAW_AUDIO_DIR, self.SNIPPETS_DIR]:
            Path(d).mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    """Cached singleton settings instance."""
    settings = Settings()
    settings.ensure_dirs()
    return settings
