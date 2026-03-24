from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.api.routes.auth import limiter
from src.config import settings
from src.generation.conversational_rag import _build_memory_context
from src.generation.intent_classifier import Action, MetadataIntent, QueryType, analyze_query_async
from src.db.models import QueryUsage
from src.generation.llm_client import generate_answer_stream, get_last_usage
from src.generation.prompt_builder import build_messages, build_metadata_messages, build_no_results_messages
from src.retrieval.metadata_retriever import fetch_metadata, get_system_knowledge_summary
from src.generation.rag_chain import run_rag_query
from src.memory.hindsight_client import (
    ensure_corrections_bank,
    ensure_quality_bank,
    ensure_user_bank,
    recall_corrections,
    recall_quality_insights,
    recall_user_memory,
    retain_interaction,
)
from src.rbac.permissions import get_user_groups, get_user_project_ids
from src.retrieval.embedder import embed_text, get_last_embed_tokens
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
@limiter.limit("30/minute")
async def query(
    request: Request,
    body: QueryRequest,
    user: CurrentUser,
    db: DBSession,
    qdrant: QdrantClient,
) -> QueryResponse:
    """Run a RAG query: embed → search → generate answer with citations."""
    user_groups = get_user_groups(user)
    project_ids = await get_user_project_ids(user, db)
    if body.project_id:
        if project_ids is not None and body.project_id not in project_ids:
            raise HTTPException(403, "No access to this project")
        project_ids = [body.project_id]
    try:
        return await run_rag_query(
            request=body,
            user_groups=user_groups,
            user_id=user.id,
            qdrant=qdrant,
            db=db,
            project_ids=project_ids,
        )
    except RuntimeError as e:
        logger.error("RAG query failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"LLM generation failed: {e}",
        )


@router.post("/query/debug", response_model=DebugSearchResponse)
async def query_debug(
    body: DebugSearchRequest,
    user: CurrentUser,
    db: DBSession,
    qdrant: QdrantClient,
) -> DebugSearchResponse:
    """Return raw Qdrant scores without threshold filtering for diagnostics."""
    user_groups = get_user_groups(user)
    project_ids = await get_user_project_ids(user, db)

    embed_start = time.perf_counter()
    query_embedding = await embed_text(body.query)
    embedding_ms = int((time.perf_counter() - embed_start) * 1000)

    from src.retrieval.search import _build_search_filter
    search_filter = _build_search_filter(user_groups, project_ids=project_ids)
    rbac_filter_applied = search_filter is not None

    results = await qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        using="dense",
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
@limiter.limit("30/minute")
async def query_stream(
    request: Request,
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
    project_ids = await get_user_project_ids(user, db)
    if body.project_id:
        if project_ids is not None and body.project_id not in project_ids:
            raise HTTPException(403, "No access to this project")
        project_ids = [body.project_id]
    start = time.perf_counter()
    uid = str(user.id)

    # 0. Recall memories from Hindsight (non-blocking, empty when disabled)
    await ensure_user_bank(uid)
    await ensure_quality_bank()
    await ensure_corrections_bank()
    user_memories, quality_insights, corrections = await asyncio.gather(
        recall_user_memory(uid, body.query),
        recall_quality_insights(body.query),
        recall_corrections(body.query),
    )
    memory_context = _build_memory_context(user_memories, quality_insights, corrections)
    if memory_context:
        logger.info("Stream: Injecting %d user memories + %d quality insights + %d corrections",
                     len(user_memories), len(quality_insights), len(corrections))

    # 1. Route query
    analysis = await analyze_query_async(body.query)

    # Fetch system knowledge for injection
    system_knowledge: str | None = None
    if settings.system_knowledge_injection:
        try:
            system_knowledge = await get_system_knowledge_summary(db, project_ids)
        except Exception as e:
            logger.warning("Stream: Failed to fetch system knowledge: %s", e)

    if analysis.action == Action.METADATA:
        # Metadata query — answer from PostgreSQL
        logger.info("Stream: Metadata query: intent=%s, project=%s",
                     analysis.metadata_intent, analysis.extracted_project)
        metadata = await fetch_metadata(
            db, analysis.metadata_intent, project_ids, analysis.extracted_project,
        )

        # For HYBRID: also run vector search
        hybrid_sources = []
        if analysis.metadata_intent == MetadataIntent.HYBRID:
            hybrid_sources = await search_similar(
                qdrant, analysis.cleaned_query, user_groups,
                top_k=body.top_k, threshold=body.threshold,
                project_ids=project_ids,
            )

        if hybrid_sources:
            document_sources = merge_sources_by_document(hybrid_sources)
            ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
            messages = build_metadata_messages(
                body.query, metadata.text,
                sources=hybrid_sources, reference_numbers=ref_map,
                memory_context=memory_context,
            )
            sources_data = [s.model_dump() for s in document_sources]
        else:
            document_sources = []
            messages = build_metadata_messages(
                body.query, metadata.text, memory_context=memory_context,
            )
            sources_data = []

    elif analysis.action == Action.RESPOND:
        messages = build_no_results_messages(body.query, memory_context=memory_context, system_knowledge=system_knowledge)
        sources_data = []
        document_sources = []
    elif analysis.query_type == QueryType.SUMMARY:
        # Summaries are too complex to stream (map-reduce), fall back to non-streaming
        result = await run_rag_query(body, user_groups, user.id, qdrant, db, project_ids=project_ids)
        async def _non_stream():
            yield f"event: token\ndata: {json.dumps(result.answer)}\n\n"
            yield f"event: sources\ndata: {json.dumps([s.model_dump() for s in result.sources])}\n\n"
            yield "event: done\ndata: {}\n\n"
        return StreamingResponse(_non_stream(), media_type="text/event-stream")
    else:
        # 2. Search (with project-level RBAC)
        doc_ids = body.document_ids
        if settings.query_expansion_enabled:
            sources = await search_multi_query(
                qdrant, analysis.cleaned_query, user_groups,
                top_k=body.top_k, threshold=body.threshold,
                document_ids=doc_ids, project_ids=project_ids,
            )
        elif settings.reranker_enabled:
            sources = await search_and_rerank(
                qdrant, analysis.cleaned_query, user_groups,
                top_k=body.top_k, threshold=body.threshold,
                document_ids=doc_ids, project_ids=project_ids,
            )
        elif settings.hybrid_search_enabled:
            sources = await search_similar_hybrid(
                qdrant, analysis.cleaned_query, user_groups,
                top_k=body.top_k, threshold=body.threshold,
                document_ids=doc_ids, project_ids=project_ids,
            )
        else:
            sources = await search_similar(
                qdrant, analysis.cleaned_query, user_groups,
                top_k=body.top_k, threshold=body.threshold,
                document_ids=doc_ids, project_ids=project_ids,
            )

        if sources:
            document_sources = merge_sources_by_document(sources)
            ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
            messages = build_messages(body.query, sources, reference_numbers=ref_map, memory_context=memory_context, system_knowledge=system_knowledge)
            sources_data = [s.model_dump() for s in document_sources]
        else:
            messages = build_no_results_messages(body.query, memory_context=memory_context, system_knowledge=system_knowledge)
            sources_data = []
            document_sources = []

    # 3. Stream response
    async def event_generator():
        try:
            # Send sources first so frontend can display them
            yield f"event: sources\ndata: {json.dumps(sources_data)}\n\n"

            # Stream LLM tokens and accumulate answer for retain
            accumulated = ""
            async for token in generate_answer_stream(messages):
                accumulated += token
                yield f"event: token\ndata: {json.dumps(token)}\n\n"

            latency_ms = int((time.perf_counter() - start) * 1000)

            # Emit usage event (token counts + cost)
            usage = get_last_usage()
            usage_data = {
                "input_tokens": usage.input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
                "model": usage.model if usage else "",
                "provider": usage.provider if usage else "",
                "cost_usd": usage.cost_usd if usage else 0.0,
                "latency_ms": latency_ms,
            }
            yield f"event: usage\ndata: {json.dumps(usage_data)}\n\n"

            # Persist usage to DB (fire-and-forget)
            if usage:
                try:
                    entry = QueryUsage(
                        user_id=user.id,
                        interface="rag_query",
                        query_text=body.query[:500],
                        model_name=usage.model,
                        provider=usage.provider,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        embedding_tokens=get_last_embed_tokens(),
                        cost_usd=usage.cost_usd,
                        latency_ms=latency_ms,
                        llm_ms=usage.latency_ms,
                    )
                    db.add(entry)
                    await db.commit()
                except Exception as db_err:
                    logger.warning("Failed to persist query usage: %s", db_err)

            # Retain interaction to Hindsight (fire-and-forget — does not block response)
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
        except Exception as e:
            logger.error("Streaming query failed: %s", e, exc_info=True)
            yield f"event: error\ndata: {json.dumps(str(e))}\n\n"
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
