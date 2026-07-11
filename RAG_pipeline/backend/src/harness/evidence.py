"""Read-only evidence capability execution for the Nexus harness."""

from __future__ import annotations

import datetime
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.harness.models import (
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    EXTERNAL_CONNECTOR_READ_CAPABILITY,
    RUNTIME_EVIDENCE_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
)
from src.harness.providers.mcp_docs import McpDocumentationProvider, McpDocumentationProviderError
from src.retrieval.search import search_and_rerank, search_similar
from src.runtime.client import execute_runtime_tool
from src.runtime.models import RuntimeToolCall
from src.runtime.source_registry import all_source_providers
from src.runtime.store import RuntimeStoreRepository


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
    if settings.reranker_enabled:
        sources = await search_and_rerank(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            top_k=top_k,
            threshold=min_score,
            project_ids=project_ids,
            document_roles=["source"],
        )
    else:
        sources = await search_similar(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            top_k=top_k,
            threshold=min_score,
            project_ids=project_ids,
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


async def query_runtime_evidence(
    *,
    question: str,
    project_id: str,
    project_scope: list[str] | None,
    db: AsyncSession,
) -> EvidenceQueryResult:
    """Query existing runtime tools and normalize the safe outputs."""
    _ = question
    calls = [
        RuntimeToolCall(
            tool_name="list_runtime_sync_status",
            arguments={"project_id": project_id, "limit": 5},
        ),
        RuntimeToolCall(tool_name="detect_failures", arguments={"limit": 5}),
        RuntimeToolCall(
            tool_name="list_runtime_health",
            arguments={"project_id": project_id, "limit": 5},
        ),
    ]
    items: list[EvidenceItem] = []
    errors: list[str] = []
    for call in calls:
        result = await execute_runtime_tool(call, project_scope=project_scope, db=db)
        if not result.success:
            if result.error:
                errors.append(f"{call.tool_name}: {result.error.message}")
            continue
        items.extend(_runtime_items(call.tool_name, result.data, project_id))

    status = "succeeded" if items else ("failed" if errors else "succeeded")
    return EvidenceQueryResult(
        capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
        status=status,
        items=items,
        error_message="; ".join(errors)[:500] if errors and not items else None,
    )


async def read_connector_evidence(
    *,
    db: AsyncSession,
    project_scope: list[str] | None,
    project_id: str | None = None,
) -> EvidenceQueryResult:
    """Read connector/source metadata via the existing runtime source store."""
    repo = RuntimeStoreRepository(db)
    scope = _intersect_scope(project_scope, project_id)
    sources = await repo.list_source_configs(scope, project_id=project_id, limit=50)
    provider_labels = {
        provider.provider_key(): provider.provider_label()
        for provider in all_source_providers()
    }
    items = [
        EvidenceItem(
            id=f"connector:{source.id}",
            capability_id=EXTERNAL_CONNECTOR_READ_CAPABILITY.id,
            source_type="connector_metadata",
            title=source.label,
            summary=(
                f"{provider_labels.get(source.source_type, source.source_type)} "
                f"source is {source.status}."
            ),
            uri=f"/admin/runtime?source_id={source.id}",
            observed_at=source.last_seen_at or source.last_synced_at or source.ingested_at,
            metadata={
                "source_id": source.id,
                "project_id": source.project_id,
                "source_key": source.source_key,
                "source_type": source.source_type,
                "status": source.status,
                "enabled": source.enabled,
                "is_demo": source.is_demo,
            },
        )
        for source in sources
    ]
    return EvidenceQueryResult(
        capability_id=EXTERNAL_CONNECTOR_READ_CAPABILITY.id,
        status="succeeded",
        items=items,
    )


def latest_evidence_timestamp(items: list[EvidenceItem]) -> datetime.datetime | None:
    parsed = [_parse_datetime(item.observed_at) for item in items]
    parsed = [value for value in parsed if value is not None]
    return max(parsed) if parsed else None


def evidence_refs(items: list[EvidenceItem], limit: int = 8) -> list[str]:
    return [item.id for item in items[:limit]]


def _runtime_items(tool_name: str, data: Any, project_id: str) -> list[EvidenceItem]:
    if not isinstance(data, list):
        return []
    if tool_name == "detect_failures":
        return [
            EvidenceItem(
                id=f"runtime-failure:{item.get('execution_id') or index}",
                capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
                source_type="runtime_failure",
                title=f"Runtime failure: {item.get('job_name') or 'unknown job'}",
                summary=str(item.get("error_summary") or "Runtime failure detected."),
                observed_at=item.get("failed_at"),
                metadata={
                    "project_id": project_id,
                    "execution_id": item.get("execution_id"),
                    "job_name": item.get("job_name"),
                    "failed_at": item.get("failed_at"),
                },
            )
            for index, item in enumerate(data[:5], 1)
            if isinstance(item, dict)
        ]
    if tool_name == "list_runtime_sync_status":
        return [
            EvidenceItem(
                id=f"runtime-sync:{item.get('source_key') or index}:{item.get('project_id') or project_id}",
                capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
                source_type="runtime_sync_status",
                title=f"Runtime sync: {item.get('source_key') or 'source'}",
                summary=f"Sync status is {item.get('status') or 'unknown'}.",
                observed_at=item.get("last_synced_at") or item.get("updated_at"),
                metadata={
                    "project_id": item.get("project_id") or project_id,
                    "source_key": item.get("source_key"),
                    "status": item.get("status"),
                    "last_synced_at": item.get("last_synced_at"),
                },
            )
            for index, item in enumerate(data[:5], 1)
            if isinstance(item, dict)
        ]
    if tool_name == "list_runtime_health":
        return [
            EvidenceItem(
                id=f"runtime-health:{item.get('job_name') or item.get('source_key') or index}",
                capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
                source_type="runtime_health",
                title=f"Runtime health: {item.get('job_name') or item.get('source_key') or 'source'}",
                summary=_runtime_health_summary(item),
                observed_at=item.get("last_failure_at") or item.get("last_seen_at"),
                metadata={
                    "project_id": item.get("project_id") or project_id,
                    "source_key": item.get("source_key"),
                    "job_name": item.get("job_name"),
                    "failure_rate": item.get("failure_rate"),
                    "current_failure_streak": item.get("current_failure_streak"),
                },
            )
            for index, item in enumerate(data[:5], 1)
            if isinstance(item, dict)
        ]
    return []


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
