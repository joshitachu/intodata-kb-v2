"""Read-only evidence capability execution for the Nexus harness."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models.document import Document
from src.harness.models import (
    CONVERSATION_SUMMARY_FACTS_CAPABILITY,
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_READ_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    DOCUMENTATION_ANSWER_CAPABILITY,
    GENERATED_DOCUMENT_READ_CAPABILITY,
    LOG_SEARCH_EVIDENCE_CAPABILITY,
    METADATA_QUERY_CAPABILITY,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
)
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.providers.mcp_docs import McpDocumentationProvider, McpDocumentationProviderError
from src.harness.trusted_answer import (
    AnswerDeltaSink,
    TrustedAnswerSink,
    TrustedRagAnswer,
)
from src.generation.rag_chain import _execute_search, run_rag_query
from src.generation.llm_client import get_documentation_provider_trace
from src.schemas import QueryRequest
from src.retrieval.search import search_and_rerank, search_similar
from src.retrieval.topology_hints import (
    TopologyRetrievalHints,
    resolve_topology_retrieval_hints,
)
from src.retrieval.metadata_retriever import (
    get_document_list,
    get_document_stats,
    get_project_detail,
    get_project_list,
    get_system_overview,
    get_user_profile,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.providers.stored import StoredRuntimeProvider
from src.runtime.registry import SearchLogsInput
from src.runtime.insights.parser import redact_credentials
from src.runtime.models import (
    DetectFailuresInput,
    ListExecutionsInput,
    ListRuntimeHealthInput,
    ListRuntimeInventoryInput,
    ListRuntimeSyncStatusInput,
    RuntimeExecutionState,
)


logger = logging.getLogger(__name__)


async def search_document_evidence(
    *,
    qdrant: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    project_ids: list[str] | None,
    top_k: int = 5,
    threshold: float | None = None,
) -> EvidenceQueryResult:
    """Search indexed generated/RAG docs through the existing retriever."""
    min_score = settings.default_threshold if threshold is None else threshold
    # Use the same operator-configured retrieval selector as the established
    # /query RAG path: query expansion, reranking, hybrid retrieval, or dense
    # retrieval. The conversational capability is an agent boundary around the
    # existing RAG pipeline, not a second reduced search implementation.
    sources = await _execute_search(
        query,
        user_groups,
        qdrant,
        top_k,
        min_score,
        None,
        project_ids,
        document_roles=["source"],
    )

    return EvidenceQueryResult(
        capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"doc:{source.document_id}:{source.chunk_index}",
                capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
                source_type="rag_document",
                title=source.display_name or source.filename,
                summary=_preview(source.text),
                uri=source.artifact_path or source.filename,
                score=source.score,
                metadata={
                    "document_id": source.document_id,
                    "chunk_index": source.chunk_index,
                    "filename": source.filename,
                    "source_layer": source.source_layer,
                    "project_id": source.project_id,
                },
            )
            for source in sources[:top_k]
        ],
    )


async def answer_document_question_evidence(
    *,
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    query: str,
    user_groups: list[str],
    user_id: int,
    project_ids: list[str] | None,
    top_k: int = 8,
    trusted_answer_sink: TrustedAnswerSink,
    answer_delta_sink: AnswerDeltaSink | None = None,
    topology_hints: TopologyRetrievalHints | None = None,
) -> EvidenceQueryResult:
    """Answer one documentation question through the existing deterministic RAG pipeline.

    Delegates to ``run_rag_query`` -- the same retrieval-and-generation path used
    by the explicit ``/query`` REST endpoint -- so the answer and its sources are
    produced without any model self-reported citation. The answer is additionally
    handed to ``trusted_answer_sink`` so the read-only turn loop can adopt it
    directly as the turn's final output, bypassing the grounding-validation step
    for this capability only.

    ``answer_delta_sink`` streams that answer as it is generated. This is the only
    capability allowed to do so, and for the same reason the validator is skipped:
    the answer is already deterministically cited, so nothing downstream rewrites
    it and the streamed text is byte-identical to the persisted one. The equality
    is asserted below rather than assumed.
    """
    request = QueryRequest(query=query, top_k=top_k)
    streaming = answer_delta_sink is not None and answer_delta_sink.enabled
    try:
        response = await run_rag_query(
            request,
            user_groups=user_groups,
            user_id=user_id,
            qdrant=qdrant,
            db=db,
            project_ids=project_ids,
            on_delta=answer_delta_sink.emit if streaming else None,
            topology_hints=topology_hints,
        )
    finally:
        # This sink is request-scoped. Capturing in ``finally`` preserves the
        # exhausted-provider audit even when no trusted answer was produced.
        trusted_answer_sink.documentation_provider_trace = (
            get_documentation_provider_trace()
        )
    if streaming:
        assert answer_delta_sink is not None
        if answer_delta_sink.streamed != response.answer:
            # Defensive: the streamed text is the concatenation of the same chunks
            # the answer is built from, so this cannot happen without a provider
            # or batching bug. Log loudly -- the authoritative replayable deltas
            # emitted after persistence will correct the display either way.
            logger.error(
                "Streamed answer diverged from the persisted answer "
                "(streamed %d chars, answer %d chars)",
                len(answer_delta_sink.streamed),
                len(response.answer),
            )
    trusted_answer_sink.answer = TrustedRagAnswer(
        content=response.answer, document_sources=response.sources
    )
    return EvidenceQueryResult(
        capability_id=DOCUMENTATION_ANSWER_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"doc:{source.document_id}:{'-'.join(str(i) for i in source.chunk_indexes)}",
                capability_id=DOCUMENTATION_ANSWER_CAPABILITY.id,
                source_type="rag_document",
                title=source.display_name or source.filename,
                summary=_preview(source.text),
                uri=source.artifact_path or source.filename,
                score=source.score,
                metadata={
                    "document_id": source.document_id,
                    "filename": source.filename,
                    "source_layer": source.source_layer,
                    "project_id": source.project_id,
                },
            )
            for source in response.sources[:top_k]
        ],
        answer_draft=response.answer[:8_000],
    )


async def read_document_evidence(
    *,
    qdrant: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    project_id: str,
    document_id: int,
    max_chunks: int = 8,
) -> EvidenceQueryResult:
    """Read relevant passages from one already-identified authorized document.

    The existing retriever owns embedding, hybrid/vector fallback, reranking,
    document-group visibility, and project filtering. Both document and project
    filters are mandatory so a model-supplied document ID cannot widen scope.
    """

    if settings.reranker_enabled:
        sources = await search_and_rerank(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            top_k=max_chunks,
            threshold=0.0,
            document_ids=[document_id],
            project_ids=[project_id],
        )
    else:
        sources = await search_similar(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            top_k=max_chunks,
            threshold=0.0,
            document_ids=[document_id],
            project_ids=[project_id],
        )
    return EvidenceQueryResult(
        capability_id=DOCUMENT_READ_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"doc:{source.document_id}:{source.chunk_index}",
                capability_id=DOCUMENT_READ_CAPABILITY.id,
                source_type="rag_document_passage",
                title=source.display_name or source.filename,
                summary=_preview(source.text, limit=2_000),
                uri=source.artifact_path or source.filename,
                score=source.score,
                metadata={
                    "project_id": source.project_id or project_id,
                    "document_id": source.document_id,
                    "chunk_index": source.chunk_index,
                    "filename": source.filename,
                    "source_layer": source.source_layer,
                },
            )
            for source in sources[:max_chunks]
        ],
    )


async def query_metadata_evidence(
    *,
    db: AsyncSession,
    query_type: str,
    project_scope: list[str] | None,
    user_id: int,
    project_id: str | None = None,
    limit: int = 20,
) -> EvidenceQueryResult:
    """Reuse structured metadata queries selected by an explicit tool argument."""

    if query_type == "my_profile":
        context = await get_user_profile(db, user_id)
    elif query_type == "project_list":
        context = await get_project_list(db, project_scope)
    elif query_type == "project_detail" and project_id:
        context = await get_project_detail(db, project_id, project_scope)
    elif query_type == "document_stats":
        context = await get_document_stats(db, project_scope, project_id)
    elif query_type == "document_list":
        context = await get_document_list(db, project_scope, project_id, limit=limit)
    elif query_type == "system_overview":
        context = await get_system_overview(db, project_scope)
    else:
        return EvidenceQueryResult(
            capability_id=METADATA_QUERY_CAPABILITY.id,
            status="failed",
            error_message="The metadata query requires an authorized project selection.",
        )
    safe_text = _preview(redact_credentials(context.text), limit=8_000)
    return EvidenceQueryResult(
        capability_id=METADATA_QUERY_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"metadata:{context.query_type}:{project_id or 'authorized-scope'}",
                capability_id=METADATA_QUERY_CAPABILITY.id,
                source_type="structured_metadata",
                title=context.source_label,
                summary=safe_text,
                uri=None,
                observed_at=datetime.datetime.now(datetime.UTC),
                metadata={
                    "project_id": project_id,
                    "query_type": context.query_type,
                    "scope": "authorized_project_scope",
                },
            )
        ],
    )


def conversation_summary_facts(
    *,
    history: list[dict[str, Any]],
    conversation_id: int,
    max_messages: int = 20,
) -> EvidenceQueryResult:
    """Return bounded, redacted dialogue facts; synthesis remains in the agent."""

    bounded = history[-min(max_messages, 20) :]
    items = []
    for index, message in enumerate(bounded, start=1):
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        message_id = message.get("message_id") or message.get("id")
        identity = str(message_id) if message_id is not None else f"bounded-{index}"
        items.append(
            EvidenceItem(
                id=f"conversation:{conversation_id}:message:{identity}",
                capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
                source_type="conversation_message",
                title=f"Conversation {role} message",
                summary=_preview(redact_credentials(content), limit=1_000),
                uri=None,
                metadata={
                    "conversation_id": conversation_id,
                    "role": role,
                    "ordinal": index,
                    "message_id": message_id,
                },
            )
        )
    return EvidenceQueryResult(
        capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
        status="succeeded",
        items=items,
    )


def build_registered_read_adapters(
    *,
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    user_groups: list[str],
    docs_provider: McpDocumentationProvider,
    conversation_history: list[dict[str, Any]],
    conversation_id: int,
    trusted_answer_sink: TrustedAnswerSink,
    answer_delta_sink: AnswerDeltaSink | None = None,
) -> dict[
    str,
    Callable[[dict[str, Any], CapabilityExecutionContext], Awaitable[EvidenceQueryResult]],
]:
    """Bind mature read services to one authenticated bounded-loop turn."""

    async def search_adapter(arguments, context):  # noqa: ANN001
        project_id = arguments.get("project_id")
        selected = await _resolve_document_project_scope(
            db=db,
            project_reference=str(project_id) if project_id is not None else None,
            authorized_scope=context.project_scope,
        )
        return await search_document_evidence(
            qdrant=qdrant,
            query=str(arguments["query"]),
            user_groups=user_groups,
            project_ids=selected,
            top_k=int(arguments.get("top_k", 5)),
        )

    async def answer_adapter(arguments, context):  # noqa: ANN001
        project_id = arguments.get("project_id")
        selected = await _resolve_document_project_scope(
            db=db,
            project_reference=str(project_id) if project_id is not None else None,
            authorized_scope=context.project_scope,
        )
        # This is addressing, not evidence: MCP resolves names using the request
        # token inside the already-authorized exact project.  A missing token,
        # multi-project scope, or provider failure returns no hints and normal
        # documentation retrieval continues unchanged.
        topology_hints = await resolve_topology_retrieval_hints(
            query=str(arguments["query"]),
            project_ids=selected,
            access_token=context.access_token,
        )
        if topology_hints is not None:
            logger.info(
                "Topology-assisted document addressing project=%s strength=%s "
                "entities=%d jobs=%d source_roots=%d",
                topology_hints.project_id,
                topology_hints.strength.value,
                len(topology_hints.matched_entity_ids),
                len(topology_hints.job_names),
                len(topology_hints.source_roots),
            )
        return await answer_document_question_evidence(
            qdrant=qdrant,
            db=db,
            query=str(arguments["query"]),
            user_groups=user_groups,
            user_id=context.user_id,
            project_ids=selected,
            top_k=int(arguments.get("top_k", 8) or 8),
            trusted_answer_sink=trusted_answer_sink,
            answer_delta_sink=answer_delta_sink,
            topology_hints=topology_hints,
        )

    async def read_adapter(arguments, context):  # noqa: ANN001
        del context
        return await read_document_evidence(
            qdrant=qdrant,
            query=str(arguments["query"]),
            user_groups=user_groups,
            project_id=str(arguments["project_id"]),
            document_id=int(arguments["document_id"]),
            max_chunks=int(arguments.get("max_chunks", 8)),
        )

    async def list_adapter(arguments, context):  # noqa: ANN001
        return await list_generated_doc_evidence(
            provider=docs_provider,
            project_id=str(arguments["project_id"]),
            access_token=context.access_token,
        )

    async def generated_read_adapter(arguments, context):  # noqa: ANN001
        return await read_generated_doc_evidence(
            provider=docs_provider,
            project_id=str(arguments["project_id"]),
            filename=str(arguments["filename"]),
            access_token=context.access_token,
        )

    async def metadata_adapter(arguments, context):  # noqa: ANN001
        return await query_metadata_evidence(
            db=db,
            query_type=str(arguments["query_type"]),
            project_scope=context.project_scope,
            user_id=context.user_id,
            project_id=(
                str(arguments["project_id"])
                if arguments.get("project_id") is not None
                else None
            ),
            limit=int(arguments.get("limit", 20)),
        )

    async def runtime_adapter(arguments, context):  # noqa: ANN001
        return await query_structured_runtime_evidence(
            operation=str(arguments["operation"]),
            project_id=(
                str(arguments["project_id"])
                if arguments.get("project_id") is not None
                else None
            ),
            project_scope=context.project_scope,
            db=db,
            source_key=(
                str(arguments["source_key"])
                if arguments.get("source_key") is not None
                else None
            ),
            job_name=(
                str(arguments["job_name"])
                if arguments.get("job_name") is not None
                else None
            ),
            state=(
                str(arguments["state"])
                if arguments.get("state") is not None
                else None
            ),
            since=_parse_datetime(arguments.get("since")),
            until=_parse_datetime(arguments.get("until")),
            limit=int(arguments.get("limit", 20)),
        )

    async def logs_adapter(arguments, context):  # noqa: ANN001
        return await search_runtime_log_evidence(
            question=str(arguments["question"]),
            project_scope=context.project_scope,
            db=db,
            qdrant=qdrant,
            project_id=(
                str(arguments["project_id"])
                if arguments.get("project_id") is not None
                else None
            ),
            project_ids=arguments.get("project_ids"),
            execution_id=(
                str(arguments["execution_id"])
                if arguments.get("execution_id") is not None
                else None
            ),
            since=_parse_datetime(arguments.get("since")),
            until=_parse_datetime(arguments.get("until")),
            limit=int(arguments.get("limit", 10)),
        )

    async def summary_adapter(arguments, context):  # noqa: ANN001
        if context.conversation_id != conversation_id:
            return EvidenceQueryResult(
                capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
                status="failed",
                error_message="Conversation scope does not match the authorized turn.",
            )
        return conversation_summary_facts(
            history=conversation_history,
            conversation_id=conversation_id,
            max_messages=int(arguments.get("max_messages", 20)),
        )

    return {
        DOCUMENT_SEARCH_CAPABILITY.id: search_adapter,
        DOCUMENTATION_ANSWER_CAPABILITY.id: answer_adapter,
        DOCUMENT_READ_CAPABILITY.id: read_adapter,
        DOCUMENT_LIST_CAPABILITY.id: list_adapter,
        GENERATED_DOCUMENT_READ_CAPABILITY.id: generated_read_adapter,
        METADATA_QUERY_CAPABILITY.id: metadata_adapter,
        STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id: runtime_adapter,
        LOG_SEARCH_EVIDENCE_CAPABILITY.id: logs_adapter,
        CONVERSATION_SUMMARY_FACTS_CAPABILITY.id: summary_adapter,
    }


async def list_generated_doc_evidence(
    *,
    provider: McpDocumentationProvider,
    project_id: str,
    access_token: str | None,
) -> EvidenceQueryResult:
    """List generated docs metadata through MCP."""
    try:
        docs = await provider.list_project_docs(
            project_id=project_id,
            access_token=access_token,
        )
    except McpDocumentationProviderError as exc:
        return EvidenceQueryResult(
            capability_id=DOCUMENT_LIST_CAPABILITY.id,
            status="failed",
            error_message=exc.safe_message,
        )

    return EvidenceQueryResult(
        capability_id=DOCUMENT_LIST_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"mcp-doc:{project_id}:{doc.name}",
                capability_id=DOCUMENT_LIST_CAPABILITY.id,
                source_type="generated_doc",
                title=doc.name,
                summary=f"{doc.type.upper()} documentation file, {doc.size_bytes} bytes.",
                uri=f"/projects/{project_id}/docs/{doc.name}",
                observed_at=doc.modified_at,
                metadata={
                    "project_id": project_id,
                    "type": doc.type,
                    "size_bytes": doc.size_bytes,
                    "has_pdf": doc.has_pdf,
                    "modified_at": doc.modified_at,
                },
            )
            for doc in docs
        ],
    )


async def read_generated_doc_evidence(
    *,
    provider: McpDocumentationProvider,
    project_id: str,
    filename: str,
    access_token: str | None,
) -> EvidenceQueryResult:
    """Read one exact authorized generated Markdown document through MCP."""
    try:
        content = await provider.read_project_doc(
            project_id=project_id,
            filename=filename,
            access_token=access_token,
        )
    except McpDocumentationProviderError as exc:
        return EvidenceQueryResult(
            capability_id=GENERATED_DOCUMENT_READ_CAPABILITY.id,
            status="failed",
            error_message=exc.safe_message,
        )

    uri = f"/projects/{project_id}/docs/{filename}"
    return EvidenceQueryResult(
        capability_id=GENERATED_DOCUMENT_READ_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"mcp-doc-content:{project_id}:{filename}",
                capability_id=GENERATED_DOCUMENT_READ_CAPABILITY.id,
                source_type="generated_doc_content",
                title=filename,
                summary=content[:4_000],
                uri=uri,
                metadata={"project_id": project_id, "filename": filename},
            )
        ],
        answer_draft=content[:8_000],
    )


async def query_structured_runtime_evidence(
    *,
    operation: str,
    project_scope: list[str] | None,
    db: AsyncSession,
    project_id: str | None = None,
    source_key: str | None = None,
    job_name: str | None = None,
    state: str | None = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    limit: int = 20,
) -> EvidenceQueryResult:
    """Execute one deterministic typed runtime read without a nested agent."""

    selected_scope = _intersect_scope(project_scope, project_id)
    provider = StoredRuntimeProvider()
    provider_context = RuntimeProviderContext(project_scope=selected_scope, db=db)
    try:
        if operation == "list_executions":
            values = await provider.list_executions(
                ListExecutionsInput(
                    job_name=job_name,
                    state=RuntimeExecutionState(state) if state else None,
                    since=since,
                    until=until,
                    limit=limit,
                ),
                provider_context,
            )
        elif operation == "detect_failures":
            values = await provider.detect_failures(
                DetectFailuresInput(since=since, until=until, limit=limit),
                provider_context,
            )
        elif operation == "list_runtime_health":
            values = await provider.list_runtime_health(
                ListRuntimeHealthInput(
                    source_key=source_key,
                    project_id=project_id,
                    limit=limit,
                ),
                provider_context,
            )
        elif operation == "list_runtime_sync_status":
            values = await provider.list_runtime_sync_status(
                ListRuntimeSyncStatusInput(
                    source_key=source_key,
                    project_id=project_id,
                    limit=limit,
                ),
                provider_context,
            )
        elif operation == "list_runtime_sources":
            values = await provider.list_runtime_sources(
                ListRuntimeInventoryInput(
                    source_key=source_key,
                    project_id=project_id,
                    limit=limit,
                ),
                provider_context,
            )
        else:
            return EvidenceQueryResult(
                capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
                status="failed",
                error_message="The requested runtime operation is not available.",
            )
    except (RuntimeProviderError, ValueError) as exc:
        return EvidenceQueryResult(
            capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
            status="failed",
            error_message=str(exc)[:500],
        )
    serialized = [
        value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
        for value in values
        if hasattr(value, "model_dump") or isinstance(value, dict)
    ]
    return EvidenceQueryResult(
        capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
        status="succeeded",
        items=_generic_runtime_items(
            operation,
            serialized[:limit],
            project_id,
            capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
        ),
    )


async def search_runtime_log_evidence(
    *,
    question: str,
    project_scope: list[str] | None,
    db: AsyncSession,
    qdrant: AsyncQdrantClient | None = None,
    project_id: str | None = None,
    project_ids: list[str] | None = None,
    execution_id: str | None = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    limit: int = 10,
) -> EvidenceQueryResult:
    """Search held logs through the stored provider and normalize safe evidence.

    This function is intentionally only an adapter: harness callers must invoke it
    through ``HarnessCapabilityExecutor``. The stored provider owns repository
    scope and produces credential-redacted snippets; this layer prevents internal
    row identifiers or provider payloads from becoming evidence metadata.
    """
    selected_scope = _intersect_project_scopes(project_scope, project_ids)
    if project_id is not None:
        selected_scope = _intersect_scope(selected_scope, project_id)
    try:
        params = SearchLogsInput(
            question=question,
            project_id=project_id,
            execution_id=execution_id,
            since=since,
            until=until,
            limit=limit,
        )
        rows = await StoredRuntimeProvider().search_logs(
            params,
            RuntimeProviderContext(project_scope=selected_scope, db=db),
        )
    except (RuntimeProviderError, ValueError) as exc:
        return EvidenceQueryResult(
            capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
            status="failed",
            error_message=str(exc)[:500],
        )

    items = [
        _log_search_evidence_item(row, index=index)
        for index, row in enumerate(rows[:limit], start=1)
    ]
    if settings.runtime_semantic_enabled and qdrant is not None:
        from src.runtime.semantic.service import RuntimeSemanticService

        semantic_hits = await RuntimeSemanticService(qdrant).search(
            question,
            project_ids=selected_scope,
            top_k=limit,
        )
        items.extend(
            _semantic_runtime_evidence_item(hit, index=index)
            for index, hit in enumerate(semantic_hits, start=1)
        )
    items = _dedupe_runtime_search_items(items)[:limit]
    return EvidenceQueryResult(
        capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
        status="succeeded",
        items=items,
        answer_draft=_log_search_answer(question, items),
    )


def _semantic_runtime_evidence_item(hit: Any, *, index: int) -> EvidenceItem:
    execution_id = hit.execution_id
    record_ref = execution_id or hit.incident_id or hit.signature_hash or hit.record_id
    return EvidenceItem(
        id=f"runtime-semantic:{hit.source.value}:{hit.record_id}:{index}",
        capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
        source_type=f"runtime_{hit.source.value}",
        title=hit.source.value.replace("_", " ").title(),
        summary=_preview(redact_credentials(hit.text)),
        uri=(
            f"/admin/logs/executions/{execution_id}"
            if execution_id
            else "/admin/logs/incidents"
        ),
        score=hit.score,
        metadata={
            "execution_ref": execution_id,
            "record_ref": record_ref,
            "project_id": hit.project_id,
            "retrieval": "semantic",
        },
    )


def _dedupe_runtime_search_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[tuple[str, str]] = set()
    result: list[EvidenceItem] = []
    for item in items:
        key = (item.source_type, str(item.metadata.get("record_ref") or item.id))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _log_search_evidence_item(row: dict[str, Any], *, index: int) -> EvidenceItem:
    execution_id = str(row.get("execution_id") or f"execution-{index}")
    timestamp = row.get("timestamp")
    severity = str(row.get("severity") or "info").lower()
    snippet = _preview(redact_credentials(str(row.get("snippet") or "")))
    job_name = str(row.get("job_name") or "runtime execution")
    return EvidenceItem(
        id=f"runtime-log:{execution_id}:{index}",
        capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
        source_type="runtime_log_search",
        title=f"Log match in {job_name}",
        summary=snippet,
        uri=f"/admin/logs/executions/{execution_id}",
        observed_at=timestamp,
        metadata={
            "execution_ref": execution_id,
            "project_id": row.get("project_id"),
            "source_key": row.get("source_key"),
            "severity": severity,
            "occurrence_count": row.get("occurrence_count", 1),
        },
    )


def _log_search_answer(question: str, items: list[EvidenceItem]) -> str:
    """Return a bounded question-aware draft grounded in normalized log evidence."""
    if not items:
        return "No matching runtime log evidence was found for the requested scope."
    execution_refs = list(dict.fromkeys(
        str(item.metadata["execution_ref"])
        for item in items
        if item.metadata.get("execution_ref")
    ))
    first = items[0]
    scope = ", ".join(execution_refs[:3]) or "curated runtime insights"
    more = "" if len(execution_refs) <= 3 else f" and {len(execution_refs) - 3} more"
    return (
        f"For the log search question '{_preview(redact_credentials(question), limit=160)}', I found "
        f"{len(items)} matching snippet(s) in execution(s) {scope}{more}. "
        f"Most relevant {first.metadata.get('severity', 'log')} signal: {first.summary}"
    )[:4_000]


def _generic_runtime_items(
    tool_name: str,
    data: Any,
    project_id: str | None,
    *,
    capability_id: str = STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
) -> list[EvidenceItem]:
    """Normalize arbitrary safe runtime-tool output without knowing its intent."""
    if isinstance(data, list):
        values = [item for item in data[:10] if isinstance(item, dict)]
    elif isinstance(data, dict):
        nested = next(
            (
                value
                for value in data.values()
                if isinstance(value, list) and value and isinstance(value[0], dict)
            ),
            None,
        )
        values = nested[:10] if isinstance(nested, list) else [data]
    else:
        values = []
    result = []
    for index, value in enumerate(values, 1):
        fingerprint = hashlib.sha256(
            json.dumps(value, default=str, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        title = next(
            (
                str(value[key])
                for key in (
                    "engine_name",
                    "name",
                    "job_name",
                    "plan_name",
                    "source_key",
                    "execution_id",
                )
                if value.get(key)
            ),
            tool_name.replace("_", " ").title(),
        )
        observed_at = next(
            (
                value.get(key)
                for key in (
                    "last_seen_at",
                    "last_synced_at",
                    "failed_at",
                    "started_at",
                    "ended_at",
                    "updated_at",
                )
                if value.get(key)
            ),
            None,
        )
        record_ref = next(
            (
                str(value[key])
                for key in (
                    "execution_id",
                    "incident_id",
                    "source_key",
                    "job_id",
                    "engine_id",
                    "plan_id",
                    "id",
                )
                if value.get(key) is not None
            ),
            fingerprint,
        )
        result.append(
            EvidenceItem(
                id=(
                    f"runtime:{tool_name}:"
                    f"{value.get('id') or value.get('engine_id') or value.get('execution_id') or fingerprint}"
                ),
                capability_id=capability_id,
                source_type=f"runtime_{tool_name}",
                title=title[:500],
                summary=_runtime_record_summary(value),
                observed_at=observed_at,
                metadata={
                    "project_id": value.get("project_id") or project_id,
                    "tool_name": tool_name,
                    "record_ref": record_ref,
                    "execution_ref": value.get("execution_id"),
                    "source_key": value.get("source_key"),
                },
            )
        )
    return result


def _runtime_record_summary(value: dict[str, Any]) -> str:
    safe_keys = (
        "name",
        "engine_name",
        "job_name",
        "plan_name",
        "source_key",
        "status",
        "state",
        "environment_name",
        "workspace_name",
        "version",
        "availability",
        "last_seen_at",
        "last_synced_at",
        "started_at",
        "ended_at",
        "failed_at",
        "failure_rate",
        "current_failure_streak",
        "error_summary",
    )
    safe = {key: value[key] for key in safe_keys if value.get(key) is not None}
    return _preview(json.dumps(safe, default=str, sort_keys=True), limit=1000)


def _within_time_window(
    items: list[EvidenceItem],
    *,
    since: datetime.datetime | None,
    until: datetime.datetime | None,
) -> list[EvidenceItem]:
    if since is None and until is None:
        return items
    result: list[EvidenceItem] = []
    for item in items:
        observed = _parse_datetime(item.observed_at)
        if observed is None:
            continue
        if since is not None and observed < _as_utc(since):
            continue
        if until is not None and observed >= _as_utc(until):
            continue
        result.append(item)
    return result


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _runtime_health_summary(item: dict[str, Any]) -> str:
    parts = []
    if item.get("failure_rate") is not None:
        parts.append(f"failure rate {item['failure_rate']}")
    if item.get("current_failure_streak") is not None:
        parts.append(f"failure streak {item['current_failure_streak']}")
    if item.get("last_failure_at"):
        parts.append(f"last failure {item['last_failure_at']}")
    return ", ".join(parts) if parts else "Runtime health signal."


def _intersect_scope(scope: list[str] | None, project_id: str | None) -> list[str] | None:
    if project_id is None:
        return scope
    if scope is None:
        return [project_id]
    if project_id in scope:
        return [project_id]
    return []


async def _resolve_document_project_scope(
    *,
    db: AsyncSession,
    project_reference: str | None,
    authorized_scope: list[str] | None,
) -> list[str] | None:
    """Resolve one case-insensitive reference to the exact indexed project ID.

    A selected UI project already arrives as an exact authorized identifier and
    takes the zero-query path. Model-authored references are matched only
    against the caller's authorized IDs, or for unrestricted callers against
    distinct project IDs that actually exist in the document index. No match or
    a case-fold collision fails closed as an empty scope.
    """

    if project_reference is None:
        return authorized_scope
    reference = project_reference.strip()
    if not reference or authorized_scope == []:
        return []

    if authorized_scope is not None:
        if reference in authorized_scope:
            return [reference]
        matches = {
            candidate
            for candidate in authorized_scope
            if candidate.casefold() == reference.casefold()
        }
        return [matches.pop()] if len(matches) == 1 else []

    exact = await db.execute(
        select(Document.project_id)
        .where(
            Document.deleted_at.is_(None),
            Document.project_id == reference,
        )
        .distinct()
        .limit(2)
    )
    exact_matches = {value for value in exact.scalars().all() if value}
    if len(exact_matches) == 1:
        return [exact_matches.pop()]

    folded = await db.execute(
        select(Document.project_id)
        .where(
            Document.deleted_at.is_(None),
            Document.project_id.is_not(None),
            func.lower(Document.project_id) == reference.lower(),
        )
        .distinct()
        .limit(2)
    )
    folded_matches = {value for value in folded.scalars().all() if value}
    return [folded_matches.pop()] if len(folded_matches) == 1 else []


def _intersect_project_scopes(
    authorized_scope: list[str] | None, selected_project_ids: list[str] | None
) -> list[str] | None:
    """Apply an explicit runtime selection without widening the caller's RBAC scope."""

    if selected_project_ids is None:
        return authorized_scope
    selected = list(dict.fromkeys(selected_project_ids))
    if authorized_scope is None:
        return selected
    return [project_id for project_id in selected if project_id in authorized_scope]


def _preview(text: str, limit: int = 360) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "..."


def _parse_datetime(value: datetime.datetime | str | None) -> datetime.datetime | None:
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
