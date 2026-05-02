"""
VoiceInsight AI — V1 API Router

Aggregates all endpoint routers under the /v1 prefix.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import upload, insights, query

router = APIRouter(prefix="/v1")

router.include_router(upload.router, tags=["upload"])
router.include_router(insights.router, tags=["insights"])
router.include_router(query.router, tags=["query"])
