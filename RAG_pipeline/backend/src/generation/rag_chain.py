from __future__ import annotations

import asyncio
import logging
import time

import tiktoken
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import QueryLog
from src.document_roles import AUXILIARY_DOCUMENT_ROLES
from src.memory.context import build_memory_context
from src.generation.llm_client import generate_answer, get_last_usage
from src.retrieval.embedder import get_last_embed_tokens
from src.generation.prompt_builder import (
    build_messages,
    build_no_results_messages,
    should_use_code_assistant,
)
from src.retrieval.metadata_retriever import get_system_knowledge_summary
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


async def _recall_memories(
    uid: str,
    query: str,
    project_ids: list[str] | None,
) -> tuple[str | None, str]:
    """Run parallel Hindsight recall. Returns (memory_context, answer_style).

    Memory failure is non-fatal — returns (None, answer_style) on any exception.
    """
    try:
        await ensure_user_bank(uid)
        await ensure_quality_bank()
        await ensure_corrections_bank()
        user_memories, quality_insights, corrections = await asyncio.gather(
            recall_user_memory(uid, query),
            recall_quality_insights(query),
            recall_corrections(query),
        )
        memory_context = build_memory_context(user_memories, quality_insights, corrections)
        if memory_context:
            logger.info(
                "Injecting %d user memories + %d quality insights + %d corrections",
                len(user_memories), len(quality_insights), len(corrections),
            )
    except Exception as e:
        logger.warning("Memory recall failed (non-fatal): %s", e)
        memory_context = None
    answer_style = "code" if should_use_code_assistant(query, project_ids) else "assistant"
    return memory_context, answer_style


async def _execute_search(
    query: str,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    top_k: int,
    threshold: float,
    document_ids: list[int] | None,
    project_ids: list[str] | None,
    document_roles: list[str] | None = None,
) -> list[Source]:
    """Run the operator-configured document search implementation."""
    if settings.query_expansion_enabled:
        return await search_multi_query(
            client=qdrant, query=query, user_groups=user_groups,
            top_k=top_k, threshold=threshold,
            document_ids=document_ids, project_ids=project_ids,
            document_roles=document_roles,
        )
    if settings.reranker_enabled:
        return await search_and_rerank(
            client=qdrant, query=query, user_groups=user_groups,
            top_k=top_k, threshold=threshold,
            document_ids=document_ids, project_ids=project_ids,
            document_roles=document_roles,
        )
    if settings.hybrid_search_enabled:
        return await search_similar_hybrid(
            client=qdrant, query=query, user_groups=user_groups,
            top_k=top_k, threshold=threshold,
            document_ids=document_ids, project_ids=project_ids,
            document_roles=document_roles,
        )
    return await search_similar(
        client=qdrant, query=query, user_groups=user_groups,
        top_k=top_k, threshold=threshold,
        document_ids=document_ids, project_ids=project_ids,
        document_roles=document_roles,
    )


async def _execute_role_aware_search(
    query: str,
    request: QueryRequest,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    project_ids: list[str] | None,
) -> tuple[list[Source], list[Source]]:
    """Run source-first retrieval plus bounded auxiliary/template context."""
    source_sources = await _execute_search(
        query,
        user_groups,
        qdrant,
        request.top_k,
        request.threshold,
        request.document_ids,
        project_ids,
        document_roles=["source"],
    )
    auxiliary_sources = await _execute_search(
        query,
        user_groups,
        qdrant,
        max(1, min(4, request.top_k)),
        request.threshold,
        request.document_ids,
        project_ids,
        document_roles=list(AUXILIARY_DOCUMENT_ROLES),
    )
    return source_sources, auxiliary_sources


async def _persist_query(
    db: AsyncSession,
    user_id: int,
    uid: str,
    query_text: str,
    answer: str,
    document_sources: list[DocumentSource],
    latency_ms: int,
    action: str,
    query_type: str,
    project_ids: list[str] | None,
) -> None:
    """Persist QueryUsage + QueryLog and fire-and-forget Hindsight retention.

    Non-blocking: each step catches its own exceptions so a logging failure
    never surfaces to the caller.
    """
    usage = get_last_usage()
    if usage:
        try:
            from src.db.models import QueryUsage
            db.add(QueryUsage(
                user_id=user_id,
                interface="rag_query",
                query_text=query_text[:500],
                model_name=usage.model,
                provider=usage.provider,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                embedding_tokens=get_last_embed_tokens(),
                cost_usd=usage.cost_usd,
                latency_ms=latency_ms,
                llm_ms=usage.latency_ms,
            ))
        except Exception as e:
            logger.warning("Failed to persist query usage: %s", e)

    source_names = [s.filename for s in document_sources] if document_sources else []
    asyncio.create_task(retain_interaction(
        user_id=uid,
        query=query_text,
        answer=answer,
        sources_used=source_names,
        search_result_count=len(document_sources),
        latency_ms=latency_ms,
        action=action,
        query_type=query_type,
        project_ids=project_ids,
    ))

    try:
        db.add(QueryLog(
            user_id=user_id,
            query_text=query_text,
            response_text=answer,
            sources=[s.model_dump() for s in document_sources],
            latency_ms=latency_ms,
        ))
    except Exception as e:
        logger.warning("Failed to persist query log: %s", e)


async def run_rag_query(
    request: QueryRequest,
    user_groups: list[str],
    user_id: int,
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    project_ids: list[str] | None = None,
) -> QueryResponse:
    """Run the explicit RBAC-scoped document retrieval/generation API."""
    start = time.perf_counter()
    uid = str(user_id)

    memory_context, answer_style = await _recall_memories(uid, request.query, project_ids)

    document_sources: list[DocumentSource] = []

    system_knowledge: str | None = None
    if settings.system_knowledge_injection:
        try:
            system_knowledge = await get_system_knowledge_summary(db, project_ids)
        except Exception as e:
            logger.warning("Failed to fetch system knowledge: %s", e)

    sources, auxiliary_sources = await _execute_role_aware_search(
        request.query, request, user_groups, qdrant, project_ids,
    )

    if sources or auxiliary_sources:
        logger.info("Document search returned %d source chunks", len(sources))
        enc = tiktoken.get_encoding("cl100k_base")
        total_context_tokens = sum(len(enc.encode(source.text)) for source in sources)
        logger.info(
            "Document query retrieved %d chunks and ~%d context tokens",
            len(sources),
            total_context_tokens,
        )
        document_sources = merge_sources_by_document([*sources, *auxiliary_sources])
        ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
        messages = build_messages(
            request.query,
            sources,
            reference_numbers=ref_map,
            memory_context=memory_context,
            system_knowledge=system_knowledge,
            answer_style=answer_style,
            auxiliary_sources=auxiliary_sources,
        )
    else:
        logger.info("No document results passed the configured threshold")
        messages = build_no_results_messages(
            request.query,
            memory_context=memory_context,
            system_knowledge=system_knowledge,
            answer_style=answer_style,
        )
    answer = await generate_answer(messages)

    latency_ms = int((time.perf_counter() - start) * 1000)

    logger.info(f"Completed in {latency_ms}ms | {len(document_sources)} document sources returned")

    await _persist_query(
        db=db,
        user_id=user_id,
        uid=uid,
        query_text=request.query,
        answer=answer,
        document_sources=document_sources,
        latency_ms=latency_ms,
        action="document_search",
        query_type="document_answer",
        project_ids=project_ids,
    )

    return QueryResponse(
        answer=answer,
        sources=document_sources,
        latency_ms=latency_ms,
    )
