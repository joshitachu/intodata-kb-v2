from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from qdrant_client.models import Filter, FieldCondition, MatchAny, NamedVector

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.config import settings
from src.generation.conversational_rag import _build_memory_context
from src.generation.intent_classifier import Action, QueryType, analyze_query
from src.generation.llm_client import generate_answer_stream
from src.generation.prompt_builder import build_messages, build_no_results_messages
from src.generation.rag_chain import run_rag_query
from src.memory.hindsight_client import (
    ensure_quality_bank,
    ensure_user_bank,
    recall_quality_insights,
    recall_user_memory,
    retain_interaction,
)
from src.rbac.permissions import get_user_groups
from src.retrieval.embedder import embed_text
from src.retrieval.search import (
    merge_sources_by_document,
    search_and_rerank,
    search_multi_query,
    search_similar,
    search_similar_hybrid,
)
from src.retrieval.vector_store import COLLECTION_NAME
from src.schemas import (
    DebugSearchRequest,
    DebugSearchResponse,
    DebugSearchResult,
    QueryRequest,
    QueryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    user: CurrentUser,
    db: DBSession,
    qdrant: QdrantClient,
) -> QueryResponse:
    """Run a RAG query: embed → search → generate answer with citations."""
    user_groups = get_user_groups(user)
    return await run_rag_query(
        request=body,
        user_groups=user_groups,
        user_id=user.id,
        qdrant=qdrant,
        db=db,
    )


@router.post("/query/debug", response_model=DebugSearchResponse)
async def query_debug(
    body: DebugSearchRequest,
    user: CurrentUser,
    qdrant: QdrantClient,
) -> DebugSearchResponse:
    """Return raw Qdrant scores without threshold filtering for diagnostics."""
    user_groups = get_user_groups(user)

    embed_start = time.perf_counter()
    query_embedding = await embed_text(body.query)
    embedding_ms = int((time.perf_counter() - embed_start) * 1000)

    rbac_filter_applied = bool(user_groups) and "admin" not in user_groups
    search_filter = Filter(
        must=[
            FieldCondition(
                key="allowed_groups",
                match=MatchAny(any=user_groups),
            )
        ]
    ) if rbac_filter_applied else None

    results = await qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        query_filter=search_filter,
        limit=body.top_k,
        with_payload=True,
    )

    debug_results = [
        DebugSearchResult(
            document_id=point.payload["document_id"],
            filename=point.payload["filename"],
            chunk_index=point.payload["chunk_index"],
            score=round(point.score, 4),
            text=point.payload["text"],
        )
        for point in results.points
    ]

    return DebugSearchResponse(
        query=body.query,
        rbac_groups=user_groups,
        rbac_filter_applied=rbac_filter_applied,
        top_k=body.top_k,
        embedding_ms=embedding_ms,
        total_results=len(debug_results),
        results=debug_results,
    )


@router.post("/query/stream")
async def query_stream(
    body: QueryRequest,
    user: CurrentUser,
    db: DBSession,
    qdrant: QdrantClient,
) -> StreamingResponse:
    """Streaming RAG query: search → build prompt → stream LLM response via SSE.

    Returns Server-Sent Events with:
    - event: token   → individual LLM tokens
    - event: sources → JSON array of DocumentSource objects
    - event: done    → signals end of stream
    """
    user_groups = get_user_groups(user)
    start = time.perf_counter()
    uid = str(user.id)

    # 0. Recall memories from Hindsight (non-blocking, empty when disabled)
    await ensure_user_bank(uid)
    await ensure_quality_bank()
    user_memories = await recall_user_memory(uid, body.query)
    quality_insights = await recall_quality_insights(body.query)
    memory_context = _build_memory_context(user_memories, quality_insights)
    if memory_context:
        logger.info("Stream: Injecting %d user memories + %d quality insights",
                     len(user_memories), len(quality_insights))

    # 1. Route query
    analysis = analyze_query(body.query)

    if analysis.action == Action.RESPOND:
        messages = build_no_results_messages(body.query, memory_context=memory_context)
        sources_data = []
        document_sources = []
    elif analysis.query_type == QueryType.SUMMARY:
        # Summaries are too complex to stream (map-reduce), fall back to non-streaming
        result = await run_rag_query(body, user_groups, user.id, qdrant, db)
        async def _non_stream():
            yield f"event: token\ndata: {json.dumps(result.answer)}\n\n"
            yield f"event: sources\ndata: {json.dumps([s.model_dump() for s in result.sources])}\n\n"
            yield "event: done\ndata: {}\n\n"
        return StreamingResponse(_non_stream(), media_type="text/event-stream")
    else:
        # 2. Search
        doc_ids = body.document_ids
        if settings.query_expansion_enabled:
            sources = await search_multi_query(
                qdrant, analysis.cleaned_query, user_groups,
                top_k=body.top_k, threshold=body.threshold, document_ids=doc_ids,
            )
        elif settings.reranker_enabled:
            sources = await search_and_rerank(
                qdrant, analysis.cleaned_query, user_groups,
                top_k=body.top_k, threshold=body.threshold, document_ids=doc_ids,
            )
        elif settings.hybrid_search_enabled:
            sources = await search_similar_hybrid(
                qdrant, analysis.cleaned_query, user_groups,
                top_k=body.top_k, threshold=body.threshold, document_ids=doc_ids,
            )
        else:
            sources = await search_similar(
                qdrant, analysis.cleaned_query, user_groups,
                top_k=body.top_k, threshold=body.threshold, document_ids=doc_ids,
            )

        if sources:
            document_sources = merge_sources_by_document(sources)
            ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
            messages = build_messages(body.query, sources, reference_numbers=ref_map, memory_context=memory_context)
            sources_data = [s.model_dump() for s in document_sources]
        else:
            messages = build_no_results_messages(body.query, memory_context=memory_context)
            sources_data = []
            document_sources = []

    # 3. Stream response
    async def event_generator():
        # Send sources first so frontend can display them
        yield f"event: sources\ndata: {json.dumps(sources_data)}\n\n"

        # Stream LLM tokens and accumulate answer for retain
        accumulated = ""
        async for token in generate_answer_stream(messages):
            accumulated += token
            yield f"event: token\ndata: {json.dumps(token)}\n\n"

        # Retain interaction to Hindsight (fire-and-forget — does not block response)
        latency_ms = int((time.perf_counter() - start) * 1000)
        source_names = [s.filename for s in document_sources] if document_sources else []
        asyncio.create_task(retain_interaction(
            user_id=uid,
            query=body.query,
            answer=accumulated,
            sources_used=source_names,
            search_result_count=len(document_sources),
            latency_ms=latency_ms,
            intent=analysis.action.value,
        ))

        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
