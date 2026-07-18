"""Guarded capability executor for Nexus harness runtimes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from src.harness import CapabilityDisabledError, UnknownCapabilityError
from src.harness.models import (
    ATTACH_GIT_SOURCE_CAPABILITY,
    ATTACH_ZIP_SOURCE_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY,
    PROJECT_STATUS_CAPABILITY,
    PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY,
    PUBLISH_INDEX_CAPABILITY,
    SOURCE_OPTIONS_CAPABILITY,
    TRIGGER_SCAN_CAPABILITY,
    CapabilityDefinition,
    HarnessRiskTier,
)
from src.harness.providers.mcp_projects import McpProjectsProvider, McpProjectsProviderError
from src.harness.providers.mcp_docs import McpDocumentationProvider, McpDocumentationProviderError
from src.harness.registry import CapabilityRegistry
from src.harness.connectors import ConnectorPolicyError, ConnectorService
from src.harness.connectors.models import ConnectorQuery
from src.harness.connectors.repository import ConnectorNotFoundError


class CapabilityExecutionContext(BaseModel):
    """Execution context enforced before any provider call."""

    user_id: int
    conversation_id: int | None = None
    project_scope: list[str] | None = None
    domain: str | None = None
    permissions: set[str] | None = None
    approvals: set[str] = Field(default_factory=set)
    access_token: str | None = None
    allow_writes_content: bool = False
    allow_external_side_effects: bool = False
    allowed_capability_ids: set[str] | None = None
    denied_capability_ids: set[str] = Field(default_factory=set)


class CapabilityExecutionRequest(BaseModel):
    capability_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class CapabilityObservation(BaseModel):
    """Sanitized result returned to the runtime."""

    capability_id: str
    status: str
    output_type: str | None = None
    summary: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    artifact_links: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    safe_details: dict[str, Any] = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status in {"succeeded", "triggered"}


class CapabilityExecutionDenied(Exception):
    """Raised when a capability request fails Nexus policy validation."""


class HarnessCapabilityExecutor:
    """Policy gate and provider dispatch for capability calls."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        docs_provider: McpDocumentationProvider | None = None,
        projects_provider: McpProjectsProvider | None = None,
        connector_service: ConnectorService | None = None,
        evidence_handlers: Mapping[
            str,
            Callable[[dict[str, Any], CapabilityExecutionContext], Awaitable[Any]],
        ] | None = None,
    ) -> None:
        self._registry = registry
        self._docs_provider = docs_provider
        self._projects_provider = projects_provider
        self._connector_service = connector_service
        self._evidence_handlers = dict(evidence_handlers or {})

    async def execute(
        self,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityObservation:
        try:
            capability = self._validate_request(request, context)
            return await self._dispatch(capability, request, context)
        except (CapabilityExecutionDenied, CapabilityDisabledError, UnknownCapabilityError) as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="denied",
                summary=str(exc),
                error_message=str(exc),
                safe_details={"reason": exc.__class__.__name__},
            )
        except McpDocumentationProviderError as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="failed",
                output_type="provider_error",
                summary=exc.safe_message,
                error_message=exc.safe_message,
                safe_details={"provider": "mcp.docs"},
            )
        except McpProjectsProviderError as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="failed",
                output_type="provider_error",
                summary=exc.safe_message,
                error_message=exc.safe_message,
                safe_details={"provider_type": "project_source"},
            )
        except (ConnectorPolicyError, ConnectorNotFoundError, KeyError, ValueError) as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="denied",
                output_type="connector_error",
                summary="The connector is unavailable for this scope.",
                error_message="The connector is unavailable for this scope.",
                safe_details={"reason": exc.__class__.__name__},
            )

    def _validate_request(
        self,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityDefinition:
        capability = self._registry.require_available(request.capability_id)
        if request.capability_id in context.denied_capability_ids:
            raise CapabilityExecutionDenied("Capability denied by the turn evidence policy")
        if (
            context.allowed_capability_ids is not None
            and request.capability_id not in context.allowed_capability_ids
        ):
            raise CapabilityExecutionDenied("Capability is outside the turn evidence policy")
        self._validate_permissions(capability, context)
        self._validate_risk(capability, context)
        self._validate_schema(capability, request.arguments)
        self._validate_project_scope(request.arguments, context)
        return capability

    def _validate_permissions(
        self,
        capability: CapabilityDefinition,
        context: CapabilityExecutionContext,
    ) -> None:
        if context.permissions is None:
            return
        required = set(capability.required_permissions)
        if required and not required.issubset(context.permissions):
            missing = ", ".join(sorted(required - context.permissions))
            raise CapabilityExecutionDenied(f"Missing required permission: {missing}")

    def _validate_risk(
        self,
        capability: CapabilityDefinition,
        context: CapabilityExecutionContext,
    ) -> None:
        if capability.risk_tier == HarnessRiskTier.WRITES_CONTENT:
            if not context.allow_writes_content and capability.id not in context.approvals:
                raise CapabilityExecutionDenied("Approval required before writing documentation content")
        if capability.risk_tier == HarnessRiskTier.EXTERNAL_SIDE_EFFECT:
            if not context.allow_external_side_effects and capability.id not in context.approvals:
                raise CapabilityExecutionDenied("Approval required before external side effects")

    def _validate_schema(
        self,
        capability: CapabilityDefinition,
        arguments: Mapping[str, Any],
    ) -> None:
        schema = capability.input_schema or {}
        for field_name in schema.get("required", []):
            if field_name not in arguments or arguments[field_name] is None:
                raise CapabilityExecutionDenied(f"Missing required input: {field_name}")

        properties = schema.get("properties", {})
        for field_name, value in arguments.items():
            expected = properties.get(field_name)
            if expected is None:
                if schema.get("additionalProperties") is False:
                    raise CapabilityExecutionDenied(f"Unexpected input: {field_name}")
                continue
            if not _matches_json_schema_type(value, expected.get("type")):
                raise CapabilityExecutionDenied(f"Invalid input type for: {field_name}")

    def _validate_project_scope(
        self,
        arguments: Mapping[str, Any],
        context: CapabilityExecutionContext,
    ) -> None:
        selected = []
        project_id = arguments.get("project_id")
        if project_id is not None:
            selected.append(str(project_id))
        project_ids = arguments.get("project_ids")
        if project_ids is not None:
            if not isinstance(project_ids, list) or not all(
                isinstance(value, str) and value for value in project_ids
            ):
                raise CapabilityExecutionDenied("Invalid input type for: project_ids")
            selected.extend(project_ids)
        if not selected or context.project_scope is None:
            return
        if any(value not in context.project_scope for value in selected):
            raise CapabilityExecutionDenied("Project access required")

    async def _dispatch(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityObservation:
        evidence_handler = self._evidence_handlers.get(capability.id)
        if evidence_handler is not None:
            try:
                result = await evidence_handler(dict(request.arguments), context)
            except Exception:
                return CapabilityObservation(
                    capability_id=capability.id,
                    status="failed",
                    output_type=capability.output_type,
                    summary="The evidence source could not answer this request.",
                    error_message="The evidence source could not answer this request.",
                )
            raw_items = getattr(result, "items", result)
            items = raw_items if isinstance(raw_items, list) else []
            serialized = [
                item.model_dump(mode="json") if isinstance(item, BaseModel) else dict(item)
                for item in items
                if isinstance(item, (BaseModel, Mapping))
            ]
            result_status = str(getattr(result, "status", "succeeded"))
            error_message = getattr(result, "error_message", None)
            if result_status not in {"succeeded", "success", "completed"}:
                return CapabilityObservation(
                    capability_id=capability.id,
                    status="failed",
                    output_type=capability.output_type,
                    summary="The evidence source could not answer this request.",
                    error_message=(
                        str(error_message)[:500]
                        if error_message
                        else "The evidence source returned no usable evidence."
                    ),
                    safe_details={"evidence_count": len(serialized)},
                )
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=f"Retrieved {len(serialized)} evidence item(s).",
                data={"items": serialized},
                safe_details={"evidence_count": len(serialized)},
            )

        if capability.provider_id == "nexus.connector":
            if self._connector_service is None:
                raise CapabilityExecutionDenied("Connector execution is not configured")
            instance_id = capability.metadata.get("connector_instance_id")
            connector_capability_id = capability.metadata.get("connector_capability_id")
            if not isinstance(instance_id, int) or not isinstance(connector_capability_id, str):
                raise CapabilityExecutionDenied("Connector capability metadata is invalid")
            project_id = request.arguments.get("project_id")
            result = await self._connector_service.read(
                instance_id,
                connector_capability_id,
                str(project_id) if project_id is not None else None,
                context.domain,
                ConnectorQuery(
                    query=str(request.arguments["query"]),
                    limit=int(request.arguments.get("limit", 10)),
                ),
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=f"Retrieved {len(result.items)} connector evidence item(s).",
                data={
                    "items": [
                        {
                            "id": item.source_ref,
                            "source_type": "connector",
                            "title": item.title,
                            "summary": item.excerpt,
                            "uri": item.source_ref,
                            "metadata": item.metadata,
                        }
                        for item in result.items
                    ],
                    "truncated": result.truncated,
                },
                safe_details={"evidence_count": len(result.items)},
            )

        if capability.id == DOCUMENTATION_GENERATION_CAPABILITY.id:
            provider = self._docs_provider or McpDocumentationProvider()
            generation_arguments: dict[str, Any] = {
                "project_id": str(request.arguments["project_id"]),
                "access_token": context.access_token,
                "resume": bool(request.arguments.get("resume", True)),
                "investigation_model": request.arguments.get("investigation_model"),
                "model": request.arguments.get("model"),
                "write_model": request.arguments.get("write_model"),
            }
            # Pack authority is compiled by the server.  Request arguments are
            # model/user input and must never select or replace Pack policy.
            selected_pack_ids = capability.metadata.get("nexus_pack_ids")
            if selected_pack_ids:
                generation_arguments["nexus_pack_ids"] = list(selected_pack_ids)
            pack_snapshot = capability.metadata.get("nexus_pack_snapshot")
            if isinstance(pack_snapshot, dict) and "policy_id" in pack_snapshot:
                generation_arguments["nexus_pack_snapshot"] = pack_snapshot
            presentation = capability.metadata.get("nexus_pack_presentation")
            if isinstance(presentation, dict):
                generation_arguments["nexus_presentation"] = presentation
            private_analysis_packs = capability.metadata.get("nexus_private_analysis_packs")
            if isinstance(private_analysis_packs, list):
                generation_arguments["nexus_private_analysis_packs"] = private_analysis_packs
            plugin_pack_id = request.arguments.get("plugin_pack_id")
            if plugin_pack_id is not None:
                generation_arguments["plugin_pack_id"] = plugin_pack_id
            result = await provider.trigger_generation(
                **generation_arguments,
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="triggered",
                output_type=capability.output_type,
                summary=result.message,
                data={
                    "project_id": result.project_id or request.arguments["project_id"],
                    "status": result.status,
                    "run_id": request.idempotency_key,
                },
                artifact_links=[
                    {"label": "Generation Status", "url": result.status_path, "kind": "status"},
                    {"label": "Project Documentation", "url": result.docs_path, "kind": "docs"},
                ],
                safe_details={"provider_type": capability.provider_type.value},
            )

        if capability.id == PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.id:
            provider = self._docs_provider or McpDocumentationProvider()
            workflows = await provider.list_active_project_workflows(
                allowed_project_ids=context.project_scope,
                access_token=context.access_token,
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=f"Found {len(workflows)} active project workflow(s).",
                data={
                    "workflows": [
                        {
                            "project_id": item.project_id,
                            "display_name": item.display_name,
                            "project_status": item.project_status,
                            "generation_status": item.generation_status,
                            "generation_started_at": item.generation_started_at,
                        }
                        for item in workflows
                    ]
                },
                artifact_links=[
                    {
                        "label": item.display_name,
                        "url": item.docs_path,
                        "kind": "docs",
                    }
                    for item in workflows
                ],
                safe_details={"workflow_count": len(workflows)},
            )

        if capability.id == DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id:
            provider = self._docs_provider or McpDocumentationProvider()
            result = await provider.get_generation_status(
                project_id=str(request.arguments["project_id"]),
                access_token=context.access_token,
            )
            if not result.found:
                summary = f"No documentation generation run exists for {result.project_id}."
            elif result.status in {"completed", "succeeded", "ready"}:
                summary = (
                    f"Documentation generation completed for {result.project_id} "
                    f"with {result.doc_count} document(s)."
                )
            elif result.status in {"failed", "error", "cancelled"}:
                summary = f"Documentation generation {result.status} for {result.project_id}."
            else:
                summary = f"Documentation generation is {result.status} for {result.project_id}."
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=summary,
                data={
                    "project_id": result.project_id,
                    "found": result.found,
                    "external_run_id": result.run_id,
                    "status": result.status,
                    "started_at": result.started_at,
                    "completed_at": result.completed_at,
                    "doc_count": result.doc_count,
                    "quality_score": result.quality_score,
                    "run_verdict": result.run_verdict,
                    "client_ready": result.client_ready,
                    "failed_gates": result.failed_gates,
                    "error_message": result.error_message,
                },
                artifact_links=[
                    {"label": "Generation Status", "url": result.status_path, "kind": "status"},
                    {"label": "Project Documentation", "url": result.docs_path, "kind": "docs"},
                ],
                safe_details={"provider_type": capability.provider_type.value},
            )

        if capability.id == DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id:
            provider = self._docs_provider or McpDocumentationProvider()
            result = await provider.update_doc_from_evidence(
                project_id=str(request.arguments["project_id"]),
                filename=str(request.arguments["target_doc"]),
                evidence_text=str(request.arguments["evidence_text"]),
                evidence_refs=[str(item) for item in request.arguments.get("evidence_refs", [])],
                workflow_run_id=request.arguments.get("workflow_run_id"),
                section=request.arguments.get("section"),
                expected_base_sha=request.arguments.get("expected_base_sha"),
                access_token=context.access_token,
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=result.message,
                data={
                    "project_id": result.project_id,
                    "filename": result.filename,
                    "status": result.status,
                    "receipt_path": result.receipt_path,
                    "docs_path": result.docs_path,
                    "evidence_refs": result.evidence_refs,
                    "before_sha": result.before_sha,
                    "after_sha": result.after_sha,
                    "version_id": result.version_id,
                    "section": result.section,
                    "revert_path": result.revert_path,
                    "reconciliation_status": result.reconciliation_status,
                },
                artifact_links=[
                    {"label": "Updated Documentation", "url": result.docs_path, "kind": "docs"},
                    {"label": "Update Receipt", "url": result.receipt_path, "kind": "receipt"},
                    {"label": "Revert Update", "url": result.revert_path, "kind": "revert"},
                ],
            )

        if capability.id == DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY.id:
            provider = self._docs_provider or McpDocumentationProvider()
            result = await provider.revert_doc_evidence_update(
                project_id=str(request.arguments["project_id"]),
                filename=str(request.arguments["target_doc"]),
                receipt_path=str(request.arguments["receipt_path"]),
                expected_base_sha=str(request.arguments["expected_base_sha"]),
                workflow_run_id=request.arguments.get("workflow_run_id"),
                access_token=context.access_token,
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=result.message,
                data={
                    "project_id": result.project_id,
                    "filename": result.filename,
                    "status": result.status,
                    "before_sha": result.before_sha,
                    "after_sha": result.after_sha,
                    "version_id": result.version_id,
                    "reverted_receipt_path": result.reverted_receipt_path,
                    "receipt_path": result.receipt_path,
                    "docs_path": result.docs_path,
                    "reconciliation_status": result.reconciliation_status,
                },
                artifact_links=[
                    {"label": "Restored Documentation", "url": result.docs_path, "kind": "docs"},
                    {"label": "Revert Receipt", "url": result.receipt_path, "kind": "receipt"},
                ],
            )

        projects = self._projects_provider or McpProjectsProvider()
        if capability.id == SOURCE_OPTIONS_CAPABILITY.id:
            options = await projects.list_source_options(access_token=context.access_token)
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=f"Found {len(options)} project source option(s).",
                data={
                    "options": [
                        {"id": option.id, "label": option.label, "source_type": option.source_type}
                        for option in options
                    ]
                },
            )
        if capability.id == ATTACH_GIT_SOURCE_CAPABILITY.id:
            result = await projects.attach_git_repository(
                project_id=str(request.arguments["project_id"]),
                repo_url=str(request.arguments["repo_url"]),
                platform=str(request.arguments["platform"]),
                installation_id=request.arguments.get("installation_id"),
                display_name=request.arguments.get("display_name"),
                access_token=context.access_token,
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=f"Attached the project source for {result.project_id}.",
                data={
                    "project_id": result.project_id,
                    "status": result.status,
                    "platform": result.platform,
                    "source_type": result.source_type,
                },
                artifact_links=[
                    {"label": "Project", "url": result.project_path, "kind": "project"}
                ],
            )
        if capability.id == ATTACH_ZIP_SOURCE_CAPABILITY.id:
            result = await projects.verify_uploaded_zip_source(
                upload_receipt=str(request.arguments["upload_receipt"]),
                access_token=context.access_token,
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=f"Attached uploaded ZIP source for {result.project_id}.",
                data={
                    "project_id": result.project_id,
                    "platform": result.platform,
                    "source_type": result.source_type,
                    "filename": result.filename,
                    "size_bytes": result.size_bytes,
                    "sha256": result.sha256,
                },
                artifact_links=[
                    {"label": "Project", "url": result.project_path, "kind": "project"}
                ],
                safe_details={"source_type": "zip", "size_bytes": result.size_bytes},
            )
        if capability.id == TRIGGER_SCAN_CAPABILITY.id:
            result = await projects.trigger_scan(
                project_id=str(request.arguments["project_id"]),
                access_token=context.access_token,
            )
            return _project_operation_observation(capability, result, kind="scan")
        if capability.id == PROJECT_STATUS_CAPABILITY.id:
            result = await projects.get_status(
                project_id=str(request.arguments["project_id"]),
                access_token=context.access_token,
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=f"Project {result.project_id} is {result.status}.",
                data={
                    "project_id": result.project_id,
                    "status": result.status,
                    "job_count": result.job_count,
                    "doc_count": result.doc_count,
                    "last_scan_at": result.last_scan_at,
                },
                artifact_links=[
                    {"label": "Project Status", "url": result.status_path, "kind": "status"},
                    {"label": "Documentation", "url": result.docs_path, "kind": "docs"},
                ],
            )
        if capability.id == PUBLISH_INDEX_CAPABILITY.id:
            result = await projects.push_to_rag(
                project_id=str(request.arguments["project_id"]),
                access_token=context.access_token,
            )
            return _project_operation_observation(capability, result, kind="index")

        raise CapabilityExecutionDenied(f"No executor provider is registered for {capability.id}")


def _project_operation_observation(
    capability: CapabilityDefinition,
    result: Any,
    *,
    kind: str,
) -> CapabilityObservation:
    return CapabilityObservation(
        capability_id=capability.id,
        status="triggered",
        output_type=capability.output_type,
        summary=result.message,
        data={"project_id": result.project_id, "status": result.status},
        artifact_links=[
            {"label": f"{kind.title()} Status", "url": result.status_path, "kind": kind}
        ],
    )


def _matches_json_schema_type(value: Any, expected_type: Any) -> bool:
    if expected_type is None:
        return True
    expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
    for item in expected_types:
        if item == "null" and value is None:
            return True
        if item == "string" and isinstance(value, str):
            return True
        if item == "boolean" and isinstance(value, bool):
            return True
        if item == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if item == "array" and isinstance(value, list):
            return True
        if item == "object" and isinstance(value, dict):
            return True
    return False
