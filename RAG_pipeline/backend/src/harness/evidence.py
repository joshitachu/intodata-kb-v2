"""Read-only evidence capability execution for the Nexus harness."""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.generation.agent_loop import run_runtime_tool_loop
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
    project_id: str | None,
    project_scope: list[str] | None,
    db: AsyncSession,
    project_ids: list[str] | None = None,
    source_key: str | None = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
) -> EvidenceQueryResult:
    """Run the existing bounded runtime agent loop and normalize its receipts."""
    scope_lines = []
    effective_scope = _intersect_project_scopes(project_scope, project_ids)
    if project_id:
        scope_lines.append(
            f"The selected project is {project_id}. Include this project_id in tools that accept it."
        )
    elif project_ids:
        scope_lines.append(
            "The selected projects are "
            f"{', '.join(project_ids)}. Restrict every runtime tool call to those projects."
        )
    if source_key:
        scope_lines.append(f"The selected runtime source is {source_key}.")
    if since or until:
        scope_lines.append(
            "Apply this UTC time window to tools that support it: "
            f"since={since.isoformat() if since else 'unbounded'}, "
            f"until={until.isoformat() if until else 'unbounded'}."
        )
    messages = []
    if scope_lines:
        messages.append({"role": "system", "content": " ".join(scope_lines)})
    messages.append({"role": "user", "content": question})

    outcome = await run_runtime_tool_loop(
        messages,
        project_scope=effective_scope,
        db=db,
    )
    items: list[EvidenceItem] = []
    errors: list[str] = []
    tool_names: list[str] = []
    for event in outcome.progress_events:
        if event.event_type != "runtime_tool_result":
            continue
        payload = event.payload
        tool_name = str(payload.get("tool_name") or "runtime tool")
        tool_names.append(tool_name)
        if not payload.get("success"):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                errors.append(f"{tool_name}: {error['message']}")
            continue
        data = payload.get("data")
        normalized = _runtime_items(tool_name, data, project_id or "all")
        if not normalized:
            normalized = _generic_runtime_items(tool_name, data, project_id)
        items.extend(_within_time_window(normalized, since=since, until=until))

    final_answer = (outcome.final_answer or "").strip()
    if outcome.used_runtime_data and final_answer:
        items.insert(
            0,
            EvidenceItem(
                id=f"runtime-answer:{datetime.datetime.now(datetime.UTC).isoformat()}",
                capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
                source_type="runtime_answer",
                title="Runtime evidence answer",
                summary=_preview(final_answer, limit=4000),
                observed_at=datetime.datetime.now(datetime.UTC),
                metadata={
                    "project_id": project_id,
                    "runtime_answer": _preview(final_answer, limit=4000),
                    "tools_used": list(dict.fromkeys(tool_names)),
                },
            ),
        )

    if not outcome.used_runtime_data and not errors:
        errors.append("The runtime agent did not execute a runtime tool.")
    status = "succeeded" if items else "failed"
    return EvidenceQueryResult(
        capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
        status=status,
        items=items,
        error_message="; ".join(errors)[:500] if errors and not items else None,
        answer_draft=final_answer[:12_000] if outcome.used_runtime_data and final_answer else None,
    )


def _generic_runtime_items(
    tool_name: str, data: Any, project_id: str | None
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
        result.append(
            EvidenceItem(
                id=(
                    f"runtime:{tool_name}:"
                    f"{value.get('id') or value.get('engine_id') or value.get('execution_id') or fingerprint}"
                ),
                capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
                source_type=f"runtime_{tool_name}",
                title=title[:500],
                summary=_runtime_record_summary(value),
                observed_at=observed_at,
                metadata={
                    "project_id": value.get("project_id") or project_id,
                    "tool_name": tool_name,
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
