"""
VoiceInsight AI — Database Models (SQLAlchemy)
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Float, DateTime, ForeignKey, Enum as SAEnum, JSON
)
from sqlalchemy.orm import relationship
from app.core.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Insight(Base):
    """Represents a processed voice recording and its AI analysis."""

    __tablename__ = "insights"

    id = Column(String, primary_key=True, default=generate_uuid)
    task_id = Column(String, unique=True, nullable=False, index=True)

    # ── Content ─────────────────────────────────────────────────────
    title = Column(String(256), nullable=True)
    summary = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    clean_transcript = Column(Text, nullable=True)  # After filler removal
    mood = Column(String(32), nullable=True)         # calm | energetic | reflective
    tags = Column(JSON, nullable=True, default=list)  # ["tag1", "tag2", ...]

    # ── Audio ───────────────────────────────────────────────────────
    audio_url = Column(String(512), nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # ── Processing Status ───────────────────────────────────────────
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
    )
    # Status values: pending | converting | transcribing | filtering | analyzing | slicing | indexing | completed | failed
    progress = Column(Float, default=0.0)  # 0-100
    error_message = Column(Text, nullable=True)

    # ── Token Savings ───────────────────────────────────────────────
    original_word_count = Column(Float, nullable=True)
    clean_word_count = Column(Float, nullable=True)
    token_savings_percent = Column(Float, nullable=True)

    # ── Timestamps ──────────────────────────────────────────────────
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────────
    highlights = relationship("Highlight", back_populates="insight", cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Insight(id={self.id}, title={self.title}, status={self.status})>"


class Highlight(Base):
    """Represents an impactful audio segment identified by the LLM."""

    __tablename__ = "highlights"

    id = Column(String, primary_key=True, default=generate_uuid)
    insight_id = Column(String, ForeignKey("insights.id", ondelete="CASCADE"), nullable=False, index=True)

    # ── Content ─────────────────────────────────────────────────────
    text = Column(Text, nullable=False)               # Verbatim quote from transcript
    tag = Column(String(64), nullable=False)           # #Realization | #ActionItem | #Memory

    # ── Timestamps (audio position in seconds) ──────────────────────
    start_time = Column(Float, nullable=False, default=0.0)
    end_time = Column(Float, nullable=False, default=0.0)

    # ── Snippet ─────────────────────────────────────────────────────
    snippet_url = Column(String(512), nullable=True)   # URL to the sliced MP3 snippet

    # ── Relationships ───────────────────────────────────────────────
    insight = relationship("Insight", back_populates="highlights")

    def __repr__(self) -> str:
        return f"<Highlight(id={self.id}, tag={self.tag}, start={self.start_time})>"
