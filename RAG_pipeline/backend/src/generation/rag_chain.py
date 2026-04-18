from __future__ import annotations

import asyncio
import logging
import time

import tiktoken
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import QueryLog
from src.generation.conversational_rag import _build_memory_context
from src.generation.intent_classifier import Action, MetadataIntent, QueryType, analyze_query_async
from src.generation.llm_client import generate_answer, get_last_usage
from src.retrieval.embedder import get_last_embed_tokens
from src.generation.map_reduce import resolve_document_id, run_map_reduce
from src.generation.prompt_builder import build_messages, build_metadata_messages, build_no_results_messages
from src.retrieval.metadata_retriever import (
    fetch_metadata,
    get_system_knowledge_summary,
)
from src.config import settings
from src.memory.hindsight_client import (
    ensure_corrections_bank,
    ensure_quality_bank,
    ensure_user_bank,
    recall_corrections,
    recall_quality_insights,
    recall_user_memory,
    retain_interaction,
)
from src.retrieval.search import (
    merge_sources_by_document,
    search_and_rerank,
    search_multi_query,
    search_similar,
    search_similar_hybrid,
)
from src.schemas import DocumentSource, QueryRequest, QueryResponse, Source

logger = logging.getLogger(__name__)


async def _fetch_metadata(
    db: AsyncSession,
    intent: MetadataIntent | None,
    project_ids: list[str] | None,
    project_filter: str | None,
    user_id: int | None = None,
) -> "MetadataContext":
    """Dispatch to the appropriate metadata retriever based on intent."""
    return await fetch_metadata(db, intent, project_ids, project_filter, user_id=user_id)


async def run_rag_query(
    request: QueryRequest,
    user_groups: list[str],
    user_id: int,
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    project_ids: list[str] | None = None,
) -> QueryResponse:
    """Full RAG pipeline: classify → (search → build prompt) → generate → log."""
    start = time.perf_counter()

    # 0. Recall memories from Hindsight (non-blocking, empty when disabled)
    uid = str(user_id)
    await ensure_user_bank(uid)
    await ensure_quality_bank()
    await ensure_corrections_bank()
    user_memories, quality_insights, corrections = await asyncio.gather(
        recall_user_memory(uid, request.query),
        recall_quality_insights(request.query),
        recall_corrections(request.query),
    )
    memory_context = _build_memory_context(user_memories, quality_insights, corrections)
    if memory_context:
        logger.info("Injecting %d user memories + %d quality insights + %d corrections",
                     len(user_memories), len(quality_insights), len(corrections))

    # 1. Route query via heuristics (no LLM call)
    analysis = await analyze_query_async(request.query)
    logger.info(
        f"Query: {request.query[:80]!r} | Action: {analysis.action.value} "
        f"| Type: {analysis.query_type.value} | Cleaned: {analysis.cleaned_query[:80]!r}"
    )
    sources: list[Source] = []

    # Fetch system knowledge for injection into all query paths
    system_knowledge: str | None = None
    if settings.system_knowledge_injection:
        try:
            system_knowledge = await get_system_knowledge_summary(db, project_ids)
        except Exception as e:
            logger.warning("Failed to fetch system knowledge: %s", e)

    if analysis.action == Action.METADATA:
        # Metadata query — answer from PostgreSQL
        logger.info("Metadata query: intent=%s, project=%s",
                     analysis.metadata_intent, analysis.extracted_project)
        metadata = await _fetch_metadata(
            db, analysis.metadata_intent, project_ids, analysis.extracted_project,
            user_id=user_id,
        )

        # For HYBRID: also run vector search
        hybrid_sources: list[Source] = []
        if analysis.metadata_intent == MetadataIntent.HYBRID:
            hybrid_sources = await search_similar(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=request.top_k,
                threshold=request.threshold,
                project_ids=project_ids,
            )

        if hybrid_sources:
            document_sources = merge_sources_by_document(hybrid_sources)
            ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
            messages = build_metadata_messages(
                request.query, metadata.text,
                sources=hybrid_sources, reference_numbers=ref_map,
                memory_context=memory_context,
            )
        else:
            document_sources = []
            messages = build_metadata_messages(
                request.query, metadata.text, memory_context=memory_context,
            )

        answer = await generate_answer(messages)

    elif analysis.action == Action.RESPOND:
        # Chitchat — skip search, respond via unified no-results prompt
        logger.info("Skipping search (respond action)")
        messages = build_no_results_messages(request.query, memory_context=memory_context, system_knowledge=system_knowledge)
        answer = await generate_answer(messages)
    elif analysis.query_type == QueryType.SUMMARY:
        # SUMMARY — map-reduce pipeline for full-document summarization
        logger.info("Summary query detected — resolving document")
        resolved = await resolve_document_id(qdrant, analysis.cleaned_query, user_groups)
        if resolved:
            doc_id, filename = resolved
            answer, doc_source = await run_map_reduce(qdrant, doc_id, filename, user_groups)
            document_sources = [doc_source]
        else:
            logger.info("Could not resolve document for summary — falling back to no-results")
            messages = build_no_results_messages(request.query, memory_context=memory_context, system_knowledge=system_knowledge)
            answer = await generate_answer(messages)
    else:
        # QA — run search with cleaned query (project-level RBAC)
        doc_ids = request.document_ids
        if settings.query_expansion_enabled:
            sources = await search_multi_query(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=request.top_k,
                threshold=request.threshold,
                document_ids=doc_ids,
                project_ids=project_ids,
            )
        elif settings.reranker_enabled:
            sources = await search_and_rerank(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=request.top_k,
                threshold=request.threshold,
                document_ids=doc_ids,
                project_ids=project_ids,
            )
        elif settings.hybrid_search_enabled:
            sources = await search_similar_hybrid(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=request.top_k,
                threshold=request.threshold,
                document_ids=doc_ids,
                project_ids=project_ids,
            )
        else:
            sources = await search_similar(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=request.top_k,
                threshold=request.threshold,
                document_ids=doc_ids,
                project_ids=project_ids,
            )

        if sources:
            logger.info(f"Search returned {len(sources)} sources, building prompt")

            logger.info("--- Retrieved Chunks ---")
            for i, s in enumerate(sources, 1):
                logger.info(f"  #{i} [{s.filename}] score={s.score:.4f}")

            logger.info("--- LLM Input Preview ---")
            for i, s in enumerate(sources, 1):
                preview = s.text[:100].replace("\n", " ")
                logger.info(f"  Chunk {i}: {preview!r}...")

            enc = tiktoken.get_encoding("cl100k_base")
            total_context_tokens = sum(len(enc.encode(s.text)) for s in sources)
            logger.info(
                f"Logic trace: Retrieved {len(sources)} chunks; "
                f"Threshold {request.threshold} met; Sending ~{total_context_tokens} context tokens to LLM"
            )

            document_sources = merge_sources_by_document(sources)
            ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
            messages = build_messages(request.query, sources, reference_numbers=ref_map, memory_context=memory_context, system_knowledge=system_knowledge)
            answer = await generate_answer(messages)
        else:
            # No results — let LLM respond helpfully instead of hardcoded string
            logger.info("No results passed threshold — using no-results fallback")
            messages = build_no_results_messages(request.query, memory_context=memory_context, system_knowledge=system_knowledge)
            answer = await generate_answer(messages)

    latency_ms = int((time.perf_counter() - start) * 1000)

    # Persist token usage
    usage = get_last_usage()
    if usage:
        try:
            from src.db.models import QueryUsage
            entry = QueryUsage(
                user_id=user_id,
                interface="rag_query",
                query_text=request.query[:500],
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
        except Exception as e:
            logger.warning("Failed to persist query usage: %s", e)

    # Build document-level sources (empty list for chitchat/no-results)
    # METADATA path already sets document_sources above
    if analysis.action != Action.METADATA and not sources:
        document_sources = []

    logger.info(f"Completed in {latency_ms}ms | {len(document_sources)} document sources returned")

    # Retain interaction to Hindsight (fire-and-forget — does not block response)
    source_names = [s.filename for s in document_sources] if document_sources else []
    asyncio.create_task(retain_interaction(
        user_id=uid,
        query=request.query,
        answer=answer,
        sources_used=source_names,
        search_result_count=len(document_sources),
        latency_ms=latency_ms,
        action=analysis.action.value,
        query_type=analysis.query_type.value,
        project_ids=project_ids,
    ))

    # 3. Log the query
    log_entry = QueryLog(
        user_id=user_id,
        query_text=request.query,
        response_text=answer,
        sources=[s.model_dump() for s in document_sources],
        latency_ms=latency_ms,
    )
    db.add(log_entry)

    return QueryResponse(
        answer=answer,
        sources=document_sources,
        latency_ms=latency_ms,
    )
