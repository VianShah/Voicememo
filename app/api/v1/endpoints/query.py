"""
VoiceInsight AI — Query Endpoint (RAG Search)

POST /v1/query — Search across all recordings and get an AI-generated answer.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.schemas.insight import QueryRequest, QueryResponse

logger = logging.getLogger("voiceinsight.api.query")
router = APIRouter()


@router.post("/query", response_model=QueryResponse)
async def query_insights(request: QueryRequest):
    """
    RAG search: embed the query, search Pinecone, and generate an answer
    using the configured LLM provider.
    """
    settings = get_settings()

    if not settings.PINECONE_API_KEY:
        raise HTTPException(status_code=503, detail="Pinecone is not configured")

    try:
        from pinecone import Pinecone

        pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        index = pc.Index(settings.PINECONE_INDEX)

        # ── Step 1: Embed the query ─────────────────────────────────
        logger.info("Embedding query: '%s...'", request.query[:80])

        query_embedding = pc.inference.embed(
            model=settings.EMBEDDING_MODEL,
            inputs=[request.query],
            parameters={"input_type": "query"},
        )

        query_vector = list(query_embedding.data[0].values)

        # ── Step 2: Search Pinecone ─────────────────────────────────
        query_response = index.query(
            vector=query_vector,
            top_k=5,
            include_metadata=True,
        )

        # Filter low-confidence matches
        relevant_matches = [
            m for m in (query_response.matches or [])
            if (m.score or 0) > 0.5
        ]

        if not relevant_matches:
            logger.warning("No relevant matches found for query")
            return QueryResponse(
                answer="I couldn't find anything relevant in your recordings for that question."
            )

        # ── Step 3: Build context ───────────────────────────────────
        context = "\n\n---\n\n".join(
            f'[Insight: {m.metadata.get("title", "Untitled")}]\n"{m.metadata.get("text", "")}"'
            for m in relevant_matches
        )

        # ── Step 4: Generate answer via LLM ─────────────────────────
        from app.services.llm.base import get_llm_provider
        llm = get_llm_provider()
        answer = await llm.answer_query(request.query, context)

        logger.info("RAG answer generated (%d chars)", len(answer))
        return QueryResponse(answer=answer)

    except Exception as e:
        logger.error("Query failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")
