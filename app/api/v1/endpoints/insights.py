"""
VoiceInsight AI — Insights Endpoints

GET /v1/status/{task_id} — Poll task processing status
GET /v1/insights/{task_id} — Get full insight data
GET /v1/insights — List all insights
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.insight import Insight, Highlight
from app.schemas.insight import InsightResponse, HighlightResponse, TaskStatusResponse

logger = logging.getLogger("voiceinsight.api.insights")
router = APIRouter()


@router.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Poll the processing status of an audio task.

    Returns: status, progress (0-100), and error message if failed.
    """
    result = await db.execute(
        select(Insight).where(Insight.task_id == task_id)
    )
    insight = result.scalar_one_or_none()

    if not insight:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return TaskStatusResponse(
        task_id=insight.task_id,
        status=insight.status,
        progress=insight.progress or 0.0,
        error_message=insight.error_message,
    )


@router.get("/insights/{task_id}", response_model=InsightResponse)
async def get_insight(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Get the full insight data for a completed task.

    Includes title, summary, transcript, highlights with snippet URLs, etc.
    """
    result = await db.execute(
        select(Insight).where(Insight.task_id == task_id)
    )
    insight = result.scalar_one_or_none()

    if not insight:
        raise HTTPException(status_code=404, detail=f"Insight {task_id} not found")

    # Build highlight responses
    highlights = []
    for h in insight.highlights:
        highlights.append(HighlightResponse(
            id=h.id,
            text=h.text,
            tag=h.tag,
            startTime=h.start_time,
            endTime=h.end_time,
            snippetUrl=h.snippet_url,
        ))

    return InsightResponse(
        id=insight.id,
        taskId=insight.task_id,
        title=insight.title,
        summary=insight.summary,
        transcript=insight.transcript,
        cleanTranscript=insight.clean_transcript,
        mood=insight.mood,
        tags=insight.tags or [],
        audioUrl=insight.audio_url,
        duration=insight.duration_seconds,
        highlights=highlights,
        status=insight.status,
        progress=insight.progress or 0.0,
        errorMessage=insight.error_message,
        tokenSavingsPercent=insight.token_savings_percent,
        timestamp=insight.created_at,
    )


@router.get("/insights", response_model=list[InsightResponse])
async def list_insights(db: AsyncSession = Depends(get_db)):
    """
    List all insights that have viewable content, ordered by most recent first.

    Includes partially-processed insights (transcribed, filtered, analyzed)
    so users can see progressive results while processing continues.
    """
    viewable_statuses = ["transcribed", "filtered", "analyzed", "completed", "partial"]
    result = await db.execute(
        select(Insight).where(Insight.status.in_(viewable_statuses)).order_by(Insight.created_at.desc())
    )
    insights = result.scalars().all()

    responses = []
    for insight in insights:
        highlights = []
        for h in insight.highlights:
            highlights.append(HighlightResponse(
                id=h.id,
                text=h.text,
                tag=h.tag,
                startTime=h.start_time,
                endTime=h.end_time,
                snippetUrl=h.snippet_url,
            ))

        responses.append(InsightResponse(
            id=insight.id,
            taskId=insight.task_id,
            title=insight.title,
            summary=insight.summary,
            transcript=insight.transcript,
            cleanTranscript=insight.clean_transcript,
            mood=insight.mood,
            tags=insight.tags or [],
            audioUrl=insight.audio_url,
            duration=insight.duration_seconds,
            highlights=highlights,
            status=insight.status,
            progress=insight.progress or 0.0,
            errorMessage=insight.error_message,
            tokenSavingsPercent=insight.token_savings_percent,
            timestamp=insight.created_at,
        ))

    return responses
