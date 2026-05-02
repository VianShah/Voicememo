"""
VoiceInsight AI — Pydantic Schemas (API Request / Response)
"""

from pydantic import BaseModel, Field
from datetime import datetime


# ── Highlight ───────────────────────────────────────────────────────

class HighlightResponse(BaseModel):
    id: str
    text: str
    tag: str
    start_time: float = Field(alias="startTime", default=0.0)
    end_time: float = Field(alias="endTime", default=0.0)
    snippet_url: str | None = Field(alias="snippetUrl", default=None)

    model_config = {"populate_by_name": True, "from_attributes": True}


# ── Insight ─────────────────────────────────────────────────────────

class InsightResponse(BaseModel):
    id: str
    task_id: str = Field(alias="taskId")
    title: str | None = None
    summary: str | None = None
    transcript: str | None = None
    clean_transcript: str | None = Field(alias="cleanTranscript", default=None)
    mood: str | None = None
    tags: list[str] = []
    audio_url: str | None = Field(alias="audioUrl", default=None)
    duration_seconds: float | None = Field(alias="duration", default=None)
    highlights: list[HighlightResponse] = []
    status: str = "pending"
    progress: float = 0.0
    error_message: str | None = Field(alias="errorMessage", default=None)
    token_savings_percent: float | None = Field(alias="tokenSavingsPercent", default=None)
    created_at: datetime | None = Field(alias="timestamp", default=None)

    model_config = {"populate_by_name": True, "from_attributes": True}


# ── Task Status ─────────────────────────────────────────────────────

class TaskStatusResponse(BaseModel):
    task_id: str = Field(alias="taskId")
    status: str
    progress: float = 0.0
    error_message: str | None = Field(alias="errorMessage", default=None)

    model_config = {"populate_by_name": True}


class UploadResponse(BaseModel):
    task_id: str = Field(alias="taskId")

    model_config = {"populate_by_name": True}


# ── Query ───────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


class QueryResponse(BaseModel):
    answer: str
