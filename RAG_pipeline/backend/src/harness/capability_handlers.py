"""Registered handlers for the first migrated Nexus capabilities."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityHandlerServices,
    CapabilityObservation,
)
from src.harness.conversation_state import (
    ConversationEffectReceipt,
    ConversationRequestedInput,
    ConversationStateRepository,
    ConversationTaskEffect,
    ConversationTaskKind,
    ConversationTaskReference,
)
from src.harness.models import (
    ATTACH_GIT_SOURCE_CAPABILITY,
    ATTACH_ZIP_SOURCE_CAPABILITY,
    CONVERSATION_SUMMARY_FACTS_CAPABILITY,
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_READ_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    DOCUMENT_UPDATE_PROPOSAL_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_PACK_LIST_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    LOG_SEARCH_EVIDENCE_CAPABILITY,
    METADATA_QUERY_CAPABILITY,
    PACK_DRAFT_ABANDON_CAPABILITY,
    PACK_DRAFT_CREATE_CAPABILITY,
    PACK_DRAFT_MUTATE_CAPABILITY,
    PACK_DRAFT_READ_CAPABILITY,
    PACK_DRAFT_SAVE_CAPABILITY,
    PACK_DRAFT_VALIDATE_CAPABILITY,
    ONBOARDING_RESUME_CAPABILITY,
    PROJECT_ONBOARDING_CAPABILITY,
    PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY,
    PROJECT_STATUS_CAPABILITY,
    PUBLISH_INDEX_CAPABILITY,
    SOURCE_OPTIONS_CAPABILITY,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
    TRIGGER_SCAN_CAPABILITY,
    WORKFLOW_CANCELLATION_CAPABILITY,
    CapabilityDefinition,
)
from src.harness.packs.draft_service import (
    DraftNotFoundError,
    DraftRecord,
    DraftServiceError,
    PackDraftService,
)
from src.harness.connectors.models import ConnectorQuery
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.providers.mcp_projects import McpProjectsProvider
from src.runtime.insights.parser import redact_credentials


_SAFE_EVIDENCE_METADATA_KEYS = frozenset(
    {
        "conversation_id",
        "document_id",
        "chunk_index",
        "execution_ref",
        "filename",
        "has_pdf",
        "message_id",
        "modified_at",
        "ordinal",
        "project_id",
        "query_type",
        "record_ref",
        "role",
        "scope",
        "severity",
        "size_bytes",
        "source_key",
        "source_layer",
        "status",
        "tool_name",
        "type",
    }
)
_SAFE_URI_PREFIXES = ("/projects/", "/admin/logs/", "/admin/runtime")
_SECRET_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|(?:Bearer|Basic)\s+[A-Za-z0-9._+/=-]{8,})\b",
    re.IGNORECASE,
)


class DocumentationGenerationHandler:
    capability_id = DOCUMENTATION_GENERATION_CAPABILITY.id
    input_schema = DOCUMENTATION_GENERATION_CAPABILITY.input_schema
    output_type = DOCUMENTATION_GENERATION_CAPABILITY.output_type
    task_types = tuple(DOCUMENTATION_GENERATION_CAPABILITY.task_types)

    async def __call__(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        services: CapabilityHandlerServices,
    ) -> CapabilityObservation:
        provider = services.docs_provider or McpDocumentationProvider()

        async def provider_trigger() -> CapabilityObservation:
            return await self._trigger_provider(
                capability, request, context, provider
            )

        if services.conversation_task_service is not None:
            return await services.conversation_task_service.start_documentation(
                capability=capability,
                request=request,
                context=context,
                provider_trigger=provider_trigger,
            )
        return await provider_trigger()

    async def _trigger_provider(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        provider: McpDocumentationProvider,
    ) -> CapabilityObservation:
        generation_arguments: dict[str, Any] = {
            "project_id": str(request.arguments["project_id"]),
            "access_token": context.access_token,
            "resume": bool(request.arguments.get("resume", True)),
            "investigation_model": request.arguments.get("investigation_model"),
            "model": request.arguments.get("model"),
            "write_model": request.arguments.get("write_model"),
        }
        # Pack authority is compiled by the server. Request arguments never
        # replace the governed semantic/presentation stack.
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

        result = await provider.trigger_generation(**generation_arguments)
        workflow_ref = {
            "provider_id": capability.provider_id,
            "external_run_id": result.run_id,
            "project_id": result.project_id or request.arguments["project_id"],
        }
        return CapabilityObservation(
            capability_id=capability.id,
            status="triggered",
            output_type=capability.output_type,
            summary=result.message,
            data={
                "project_id": result.project_id or request.arguments["project_id"],
                "status": result.status,
                "run_id": request.idempotency_key,
                "external_run_id": result.run_id,
            },
            artifact_links=[
                {"label": "Generation Status", "url": result.status_path, "kind": "status"},
                {"label": "Project Documentation", "url": result.docs_path, "kind": "docs"},
            ],
            workflow_ref=workflow_ref,
            receipt_ref={"idempotency_key": request.idempotency_key},
            safe_details={"provider_type": capability.provider_type.value},
        )


class DocumentationGenerationStatusHandler:
    capability_id = DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id
    input_schema = DOCUMENTATION_GENERATION_STATUS_CAPABILITY.input_schema
    output_type = DOCUMENTATION_GENERATION_STATUS_CAPABILITY.output_type
    task_types = tuple(DOCUMENTATION_GENERATION_STATUS_CAPABILITY.task_types)

    async def __call__(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        services: CapabilityHandlerServices,
    ) -> CapabilityObservation:
        provider = services.docs_provider or McpDocumentationProvider()
        result = await provider.get_generation_status(
            project_id=str(request.arguments["project_id"]),
            access_token=context.access_token,
        )
        local_run = None
        if services.conversation_task_service is not None:
            local_run = await services.conversation_task_service.reconcile_documentation_status(
                context=context,
                requested_project_id=str(request.arguments["project_id"]),
                provider_project_id=result.project_id,
                external_run_id=result.run_id,
                provider_status=result.status,
                started_at=result.started_at,
                completed_at=result.completed_at,
                error_message=result.error_message,
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
            workflow_ref={
                "provider_id": capability.provider_id,
                "external_run_id": result.run_id,
                "project_id": result.project_id,
                **(
                    {
                        "workflow_run_id": local_run.id,
                        "status": local_run.status.value,
                    }
                    if local_run is not None
                    else {}
                ),
            },
            safe_details={"provider_type": capability.provider_type.value},
        )


class DocumentationPackCatalogHandler:
    capability_id = DOCUMENTATION_PACK_LIST_CAPABILITY.id
    input_schema = DOCUMENTATION_PACK_LIST_CAPABILITY.input_schema
    output_type = DOCUMENTATION_PACK_LIST_CAPABILITY.output_type
    task_types = tuple(DOCUMENTATION_PACK_LIST_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        scope = str(request.arguments.get("scope") or "all")
        items: list[dict[str, Any]] = []
        if scope in {"all", "organization"}:
            provider = services.docs_provider or McpDocumentationProvider()
            for pack in await provider.list_documentation_packs(
                access_token=context.access_token
            ):
                items.append(
                    {
                        "kind": "organization",
                        "name": pack.name,
                        "version": pack.version,
                        "description": pack.description,
                    }
                )
        if scope in {"all", "private"}:
            if services.pack_draft_service is None:
                if scope == "private":
                    raise RuntimeError("Private Pack catalog execution is not configured")
                versions = []
            else:
                versions = await services.pack_draft_service.list_versions(context.user_id)
            latest_by_pack: dict[str, Any] = {}
            for version in versions:
                latest_by_pack.setdefault(version.pack_id, version)
            for version in latest_by_pack.values():
                items.append(
                    {
                        "kind": "private",
                        "name": version.name,
                        "version": version.version,
                        "description": version.artifact.purpose,
                        "adds": [
                            document.title
                            for document in version.artifact.output_documents
                        ],
                    }
                )
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary=f"Found {len(items)} documentation Pack(s).",
            data={"items": items},
            safe_details={"pack_count": len(items)},
        )


class DocumentationUpdateHandler:
    capability_id = DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id
    input_schema = DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.input_schema
    output_type = DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.output_type
    task_types = tuple(DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        provider = services.docs_provider or McpDocumentationProvider()
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
                "evidence_refs": result.evidence_refs,
                "before_sha": result.before_sha,
                "after_sha": result.after_sha,
                "version_id": result.version_id,
                "section": result.section,
                "reconciliation_status": result.reconciliation_status,
            },
            artifact_links=[
                {"label": "Updated Documentation", "url": result.docs_path, "kind": "docs"},
                {"label": "Update Receipt", "url": result.receipt_path, "kind": "receipt"},
                {"label": "Revert Update", "url": result.revert_path, "kind": "revert"},
            ],
        )


class DocumentationUpdateProposalHandler:
    capability_id = DOCUMENT_UPDATE_PROPOSAL_CAPABILITY.id
    input_schema = DOCUMENT_UPDATE_PROPOSAL_CAPABILITY.input_schema
    output_type = DOCUMENT_UPDATE_PROPOSAL_CAPABILITY.output_type
    task_types = tuple(DOCUMENT_UPDATE_PROPOSAL_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        if services.conversation_task_service is None:
            raise RuntimeError("Documentation update workflow is not configured")
        return await services.conversation_task_service.start_documentation_update_proposal(
            capability=capability,
            request=request,
            context=context,
        )


class DocumentationRevertHandler:
    capability_id = DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY.id
    input_schema = DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY.input_schema
    output_type = DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY.output_type
    task_types = tuple(DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        provider = services.docs_provider or McpDocumentationProvider()
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
                "reconciliation_status": result.reconciliation_status,
            },
            artifact_links=[
                {"label": "Restored Documentation", "url": result.docs_path, "kind": "docs"},
                {"label": "Revert Receipt", "url": result.receipt_path, "kind": "receipt"},
            ],
        )


class BoundConnectorEvidenceHandler:
    """One bound connector definition; no capability-ID dispatch is involved."""

    def __init__(self, capability: CapabilityDefinition) -> None:
        self.capability_id = capability.id
        self.input_schema = capability.input_schema
        self.output_type = capability.output_type
        self.task_types = tuple(capability.task_types)
        self._instance_id = capability.metadata.get("connector_instance_id")
        self._connector_capability_id = capability.metadata.get(
            "connector_capability_id"
        )

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        if services.connector_service is None:
            raise RuntimeError("Connector execution is not configured")
        if not isinstance(self._instance_id, int) or not isinstance(
            self._connector_capability_id, str
        ):
            raise ValueError("Connector capability metadata is invalid")
        project_id = request.arguments.get("project_id")
        result = await services.connector_service.read(
            self._instance_id,
            self._connector_capability_id,
            str(project_id) if project_id is not None else None,
            context.domain,
            ConnectorQuery(
                query=str(request.arguments["query"]),
                limit=int(request.arguments.get("limit", 10)),
            ),
        )
        items: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []
        for item in result.items:
            source_ref = _safe_text(item.source_ref, limit=500)
            safe_uri = _safe_uri(item.source_ref)
            items.append(
                {
                    "id": source_ref,
                    "source_type": "connector",
                    "title": item.title,
                    "summary": item.excerpt,
                    "uri": safe_uri,
                    "metadata": item.metadata,
                }
            )
            references.append(
                {
                    "evidence_id": source_ref,
                    "source_type": "connector",
                    **({"uri": safe_uri} if safe_uri else {}),
                }
            )
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary=f"Retrieved {len(result.items)} connector evidence item(s).",
            data={
                "items": items,
                "truncated": result.truncated,
            },
            evidence_refs=references,
            safe_details={"evidence_count": len(result.items)},
        )


class ProjectOnboardingHandler:
    capability_id = PROJECT_ONBOARDING_CAPABILITY.id
    input_schema = PROJECT_ONBOARDING_CAPABILITY.input_schema
    output_type = PROJECT_ONBOARDING_CAPABILITY.output_type
    task_types = tuple(PROJECT_ONBOARDING_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        if services.conversation_task_service is None:
            raise RuntimeError("Onboarding task execution is not configured")
        return await services.conversation_task_service.start_onboarding(
            capability=capability,
            request=request,
            context=context,
        )


class OnboardingResumeHandler:
    capability_id = ONBOARDING_RESUME_CAPABILITY.id
    input_schema = ONBOARDING_RESUME_CAPABILITY.input_schema
    output_type = ONBOARDING_RESUME_CAPABILITY.output_type
    task_types = tuple(ONBOARDING_RESUME_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        if services.conversation_task_service is None:
            raise RuntimeError("Onboarding task execution is not configured")
        return await services.conversation_task_service.resume_onboarding(
            capability=capability,
            request=request,
            context=context,
        )


class ActiveProjectWorkflowStatusHandler:
    capability_id = PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.id
    input_schema = PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.input_schema
    output_type = PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.output_type
    task_types = tuple(PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.task_types)

    async def __call__(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        services: CapabilityHandlerServices,
    ) -> CapabilityObservation:
        del request
        provider = services.docs_provider or McpDocumentationProvider()
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
                {"label": item.display_name, "url": item.docs_path, "kind": "docs"}
                for item in workflows
            ],
            safe_details={"workflow_count": len(workflows)},
        )


class WorkflowCancellationHandler:
    capability_id = WORKFLOW_CANCELLATION_CAPABILITY.id
    input_schema = WORKFLOW_CANCELLATION_CAPABILITY.input_schema
    output_type = WORKFLOW_CANCELLATION_CAPABILITY.output_type
    task_types = tuple(WORKFLOW_CANCELLATION_CAPABILITY.task_types)

    async def __call__(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        services: CapabilityHandlerServices,
    ) -> CapabilityObservation:
        controller = services.workflow_cancellation_controller
        if controller is None:
            raise RuntimeError("Workflow cancellation is not configured")
        if context.conversation_id is None:
            raise ValueError("Conversation scope is required for workflow cancellation")
        raw_reference = request.arguments.get("workflow_reference")
        result = await controller.cancel(
            owner_user_id=context.user_id,
            project_scope=context.project_scope,
            conversation_id=context.conversation_id,
            workflow_reference=raw_reference if isinstance(raw_reference, str) else None,
            access_token=context.access_token,
        )
        run = result.run
        workflow_ref = (
            {
                "workflow_run_id": run.id,
                "project_id": run.project_id,
                "status": run.status.value,
            }
            if run is not None
            else None
        )
        outcome = result.outcome.value
        if outcome in {"provider_unavailable", "conflict"}:
            status = "failed"
        elif outcome in {"not_cancellable", "ambiguous", "no_match"}:
            status = "denied"
        else:
            status = "succeeded"
        return CapabilityObservation(
            capability_id=capability.id,
            status=status,
            output_type=capability.output_type,
            summary=result.message,
            data={
                "outcome": outcome,
                "workflow_run_id": run.id if run is not None else None,
                "project_id": run.project_id if run is not None else None,
                "workflow_status": run.status.value if run is not None else None,
            },
            retryable=outcome in {"provider_unavailable", "conflict"},
            error_code=(outcome if status in {"failed", "denied"} else None),
            error_message=(result.message if status in {"failed", "denied"} else None),
            workflow_ref=workflow_ref,
            receipt_ref={"idempotency_key": request.idempotency_key},
            safe_details={"outcome": outcome},
        )


def _projects_provider(services: CapabilityHandlerServices) -> McpProjectsProvider:
    return services.projects_provider or McpProjectsProvider()


class SourceOptionsHandler:
    capability_id = SOURCE_OPTIONS_CAPABILITY.id
    input_schema = SOURCE_OPTIONS_CAPABILITY.input_schema
    output_type = SOURCE_OPTIONS_CAPABILITY.output_type
    task_types = tuple(SOURCE_OPTIONS_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        del request
        options = await _projects_provider(services).list_source_options(
            access_token=context.access_token
        )
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary=f"Found {len(options)} project source option(s).",
            data={
                "options": [
                    {"id": item.id, "label": item.label, "source_type": item.source_type}
                    for item in options
                ]
            },
        )


class AttachGitSourceHandler:
    capability_id = ATTACH_GIT_SOURCE_CAPABILITY.id
    input_schema = ATTACH_GIT_SOURCE_CAPABILITY.input_schema
    output_type = ATTACH_GIT_SOURCE_CAPABILITY.output_type
    task_types = tuple(ATTACH_GIT_SOURCE_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        result = await _projects_provider(services).attach_git_repository(
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


class AttachZipSourceHandler:
    capability_id = ATTACH_ZIP_SOURCE_CAPABILITY.id
    input_schema = ATTACH_ZIP_SOURCE_CAPABILITY.input_schema
    output_type = ATTACH_ZIP_SOURCE_CAPABILITY.output_type
    task_types = tuple(ATTACH_ZIP_SOURCE_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        result = await _projects_provider(services).verify_uploaded_zip_source(
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


class TriggerScanHandler:
    capability_id = TRIGGER_SCAN_CAPABILITY.id
    input_schema = TRIGGER_SCAN_CAPABILITY.input_schema
    output_type = TRIGGER_SCAN_CAPABILITY.output_type
    task_types = tuple(TRIGGER_SCAN_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        result = await _projects_provider(services).trigger_scan(
            project_id=str(request.arguments["project_id"]),
            access_token=context.access_token,
        )
        return _project_operation_observation(capability, result, kind="scan")


class ProjectStatusHandler:
    capability_id = PROJECT_STATUS_CAPABILITY.id
    input_schema = PROJECT_STATUS_CAPABILITY.input_schema
    output_type = PROJECT_STATUS_CAPABILITY.output_type
    task_types = tuple(PROJECT_STATUS_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        result = await _projects_provider(services).get_status(
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


class PublishIndexHandler:
    capability_id = PUBLISH_INDEX_CAPABILITY.id
    input_schema = PUBLISH_INDEX_CAPABILITY.input_schema
    output_type = PUBLISH_INDEX_CAPABILITY.output_type
    task_types = tuple(PUBLISH_INDEX_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        result = await _projects_provider(services).push_to_rag(
            project_id=str(request.arguments["project_id"]),
            access_token=context.access_token,
        )
        return _project_operation_observation(capability, result, kind="index")


def _project_operation_observation(
    capability: CapabilityDefinition, result: Any, *, kind: str
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


class _PackDraftHandler:
    @staticmethod
    def _services(
        services: CapabilityHandlerServices,
    ) -> tuple[PackDraftService, ConversationStateRepository]:
        if services.pack_draft_service is None or services.conversation_state_repository is None:
            raise RuntimeError("Pack task execution is not configured")
        return services.pack_draft_service, services.conversation_state_repository

    @staticmethod
    def _claim(context: CapabilityExecutionContext, request: CapabilityExecutionRequest):
        if (
            context.conversation_id is None
            or context.conversation_turn_claim_id is None
            or context.conversation_turn_claim_token is None
            or request.idempotency_key is None
        ):
            raise RuntimeError("Pack task requires an authoritative turn claim")
        return (
            context.conversation_turn_claim_id,
            context.conversation_turn_claim_token,
            request.idempotency_key,
        )

    @staticmethod
    def _task(draft: DraftRecord, capability_id: str) -> ConversationTaskReference:
        return ConversationTaskReference(
            task_ref_id=f"pack:{draft.id}",
            kind=ConversationTaskKind.PACK_DRAFT,
            capability_id=capability_id,
            draft_id=draft.id,
            focused=True,
        )

    @staticmethod
    async def _require_conversation_draft(
        service: PackDraftService,
        *,
        draft_id: int,
        owner_user_id: int,
        conversation_id: int | None,
    ) -> DraftRecord:
        draft = await service.get_draft(draft_id, owner_user_id)
        if conversation_id is None or draft.conversation_id != conversation_id:
            raise DraftNotFoundError("Pack draft is unavailable in this conversation")
        return draft

    @staticmethod
    def _requested(
        task: ConversationTaskReference, draft: DraftRecord
    ) -> list[ConversationRequestedInput]:
        validation = draft.validation or {}
        if validation.get("valid") is True:
            return []
        errors = [
            " ".join(item.split())[:160]
            for item in validation.get("errors") or []
            if isinstance(item, str) and item.strip()
        ][:5]
        detail = "; ".join(errors)
        return [
            ConversationRequestedInput(
                task_ref_id=task.task_ref_id,
                field="pack_details",
                prompt=(
                    "Provide the missing Pack purpose, analyses, evidence, outputs, "
                    "and audience details."
                    + (f" Current validation: {detail}" if detail else "")
                )[:500],
            )
        ]

    @staticmethod
    def _observation(
        capability: CapabilityDefinition,
        draft: DraftRecord,
        *,
        summary: str,
        task: ConversationTaskReference | None = None,
    ) -> CapabilityObservation:
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary=summary,
            data={
                "draft_id": draft.id,
                "revision": draft.revision,
                "status": draft.status,
                "name": draft.name,
                "artifact": draft.artifact,
                "validation": draft.validation,
            },
            artifact_links=[
                {
                    "label": draft.name,
                    "url": f"/pack?draft={draft.id}",
                    "kind": "pack_draft",
                }
            ],
            task_ref=task,
            safe_details={"draft_id": draft.id, "revision": draft.revision},
        )

    async def _record_active(
        self,
        *,
        repository: ConversationStateRepository,
        claim_id: int,
        claim_token: str,
        call_key: str,
        capability_id: str,
        draft: DraftRecord,
    ) -> ConversationTaskReference:
        task = self._task(draft, capability_id)
        await repository.record_effect(
            claim_id=claim_id,
            lease_token=claim_token,
            effect=ConversationTaskEffect(
                call_key=call_key,
                capability_id=capability_id,
                task_ref=task,
                objective=f"Draft Analysis Pack: {draft.name}",
                requested_inputs=self._requested(task, draft),
            ),
            domain_ref={
                "draft_id": draft.id,
                "revision": draft.revision,
                "status": draft.status,
            },
        )
        return task

    async def _replay_active(
        self,
        *,
        receipt: ConversationEffectReceipt,
        service: PackDraftService,
        capability: CapabilityDefinition,
        owner_user_id: int,
        summary_prefix: str,
    ) -> CapabilityObservation:
        task = receipt.effect.task_ref
        if task is None or task.draft_id is None:
            raise RuntimeError("Pack effect receipt is missing its draft authority")
        draft = await service.get_draft(task.draft_id, owner_user_id)
        return self._observation(
            capability,
            draft,
            summary=f"{summary_prefix} Pack draft {draft.name}.",
            task=task,
        )


class PackDraftCreateHandler(_PackDraftHandler):
    capability_id = PACK_DRAFT_CREATE_CAPABILITY.id
    input_schema = PACK_DRAFT_CREATE_CAPABILITY.input_schema
    output_type = PACK_DRAFT_CREATE_CAPABILITY.output_type
    task_types = tuple(PACK_DRAFT_CREATE_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        draft_service, state_repo = self._services(services)
        claim_id, token, call_key = self._claim(context, request)
        replay = await state_repo.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            capability_id=capability.id,
            authority_hint={"kind": "pack_create"},
        )
        if replay is not None:
            return await self._replay_active(
                receipt=replay,
                service=draft_service,
                capability=capability,
                owner_user_id=context.user_id,
                summary_prefix="Created",
            )
        patch = request.arguments.get("artifact_patch")
        try:
            draft = await draft_service.create_draft(
                conversation_id=context.conversation_id,
                owner_user_id=context.user_id,
                name=str(request.arguments.get("name") or "Untitled Analysis Pack"),
                artifact_patch=dict(patch) if isinstance(patch, dict) else None,
            )
        except DraftServiceError as exc:
            await state_repo.abort_effect(
                claim_id=claim_id,
                lease_token=token,
                call_key=call_key,
                capability_id=capability.id,
                reason=str(exc),
            )
            raise
        task = await self._record_active(
            repository=state_repo,
            claim_id=claim_id,
            claim_token=token,
            call_key=call_key,
            capability_id=capability.id,
            draft=draft,
        )
        return self._observation(
            capability, draft, summary=f"Created Pack draft {draft.name}.", task=task
        )


class PackDraftReadHandler(_PackDraftHandler):
    capability_id = PACK_DRAFT_READ_CAPABILITY.id
    input_schema = PACK_DRAFT_READ_CAPABILITY.input_schema
    output_type = PACK_DRAFT_READ_CAPABILITY.output_type
    task_types = tuple(PACK_DRAFT_READ_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        if services.pack_draft_service is None:
            raise RuntimeError("Pack task execution is not configured")
        draft = await self._require_conversation_draft(
            services.pack_draft_service,
            draft_id=int(request.arguments["draft_id"]),
            owner_user_id=context.user_id,
            conversation_id=context.conversation_id,
        )
        return self._observation(
            capability,
            draft,
            summary=f"Read Pack draft {draft.name} at revision {draft.revision}.",
            task=self._task(draft, capability.id) if draft.status == "active" else None,
        )


class PackDraftMutateHandler(_PackDraftHandler):
    capability_id = PACK_DRAFT_MUTATE_CAPABILITY.id
    input_schema = PACK_DRAFT_MUTATE_CAPABILITY.input_schema
    output_type = PACK_DRAFT_MUTATE_CAPABILITY.output_type
    task_types = tuple(PACK_DRAFT_MUTATE_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        draft_service, state_repo = self._services(services)
        claim_id, token, call_key = self._claim(context, request)
        expected = int(request.arguments["expected_revision"])
        draft_id = int(request.arguments["draft_id"])
        await self._require_conversation_draft(
            draft_service,
            draft_id=draft_id,
            owner_user_id=context.user_id,
            conversation_id=context.conversation_id,
        )
        replay = await state_repo.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            capability_id=capability.id,
            authority_hint={
                "kind": "pack_revision",
                "draft_id": draft_id,
                "expected_revision": expected,
                "target_revision": expected + 1,
            },
        )
        if replay is not None:
            return await self._replay_active(
                receipt=replay,
                service=draft_service,
                capability=capability,
                owner_user_id=context.user_id,
                summary_prefix="Updated",
            )
        try:
            draft = await draft_service.mutate(
                draft_id=draft_id,
                owner_user_id=context.user_id,
                expected_revision=expected,
                patch=dict(request.arguments["artifact_patch"]),
                name=request.arguments.get("name"),
            )
        except DraftServiceError as exc:
            await state_repo.abort_effect(
                claim_id=claim_id,
                lease_token=token,
                call_key=call_key,
                capability_id=capability.id,
                reason=str(exc),
            )
            raise
        task = await self._record_active(
            repository=state_repo,
            claim_id=claim_id,
            claim_token=token,
            call_key=call_key,
            capability_id=capability.id,
            draft=draft,
        )
        return self._observation(
            capability, draft, summary=f"Updated Pack draft {draft.name}.", task=task
        )


class PackDraftValidateHandler(_PackDraftHandler):
    capability_id = PACK_DRAFT_VALIDATE_CAPABILITY.id
    input_schema = PACK_DRAFT_VALIDATE_CAPABILITY.input_schema
    output_type = PACK_DRAFT_VALIDATE_CAPABILITY.output_type
    task_types = tuple(PACK_DRAFT_VALIDATE_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        draft_service, state_repo = self._services(services)
        claim_id, token, call_key = self._claim(context, request)
        expected = int(request.arguments["expected_revision"])
        draft_id = int(request.arguments["draft_id"])
        await self._require_conversation_draft(
            draft_service,
            draft_id=draft_id,
            owner_user_id=context.user_id,
            conversation_id=context.conversation_id,
        )
        replay = await state_repo.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            capability_id=capability.id,
            authority_hint={
                "kind": "pack_revision",
                "draft_id": draft_id,
                "expected_revision": expected,
                "target_revision": expected,
            },
        )
        if replay is not None:
            return await self._replay_active(
                receipt=replay,
                service=draft_service,
                capability=capability,
                owner_user_id=context.user_id,
                summary_prefix="Validated",
            )
        try:
            draft = await draft_service.validate(draft_id, context.user_id, expected)
        except DraftServiceError as exc:
            await state_repo.abort_effect(
                claim_id=claim_id,
                lease_token=token,
                call_key=call_key,
                capability_id=capability.id,
                reason=str(exc),
            )
            raise
        task = await self._record_active(
            repository=state_repo,
            claim_id=claim_id,
            claim_token=token,
            call_key=call_key,
            capability_id=capability.id,
            draft=draft,
        )
        return self._observation(
            capability,
            draft,
            summary=(
                f"Pack draft {draft.name} is valid."
                if draft.validation.get("valid")
                else f"Pack draft {draft.name} still needs required fields."
            ),
            task=task,
        )


class PackDraftSaveHandler(_PackDraftHandler):
    capability_id = PACK_DRAFT_SAVE_CAPABILITY.id
    input_schema = PACK_DRAFT_SAVE_CAPABILITY.input_schema
    output_type = PACK_DRAFT_SAVE_CAPABILITY.output_type
    task_types = tuple(PACK_DRAFT_SAVE_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        draft_service, state_repo = self._services(services)
        claim_id, token, call_key = self._claim(context, request)
        expected = int(request.arguments["expected_revision"])
        draft_id = int(request.arguments["draft_id"])
        await self._require_conversation_draft(
            draft_service,
            draft_id=draft_id,
            owner_user_id=context.user_id,
            conversation_id=context.conversation_id,
        )
        replay = await state_repo.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            capability_id=capability.id,
            authority_hint={
                "kind": "pack_revision",
                "draft_id": draft_id,
                "expected_revision": expected,
                "target_revision": expected + 1,
            },
        )
        if replay is not None:
            version_id = replay.domain_ref.get("private_pack_version_id")
            if not isinstance(version_id, int):
                raise RuntimeError("Saved Pack receipt is missing its version authority")
            version = await draft_service.get_version(version_id, context.user_id)
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary=f"Saved private Analysis Pack {version.name} version {version.version}.",
                data={
                    "draft_id": draft_id,
                    "private_pack_version_id": version.id,
                    "pack_id": version.pack_id,
                    "name": version.name,
                    "version": version.version,
                },
                artifact_links=[
                    {"label": version.name, "url": "/pack?tab=library", "kind": "private_pack"}
                ],
            )
        try:
            version = await draft_service.save(
                draft_id=draft_id,
                owner_user_id=context.user_id,
                expected_revision=expected,
                pack_id=request.arguments.get("pack_id"),
            )
        except DraftServiceError as exc:
            await state_repo.abort_effect(
                claim_id=claim_id,
                lease_token=token,
                call_key=call_key,
                capability_id=capability.id,
                reason=str(exc),
            )
            raise
        await state_repo.record_effect(
            claim_id=claim_id,
            lease_token=token,
            effect=ConversationTaskEffect(
                call_key=call_key,
                capability_id=capability.id,
                remove_task_ref_id=f"pack:{draft_id}",
            ),
            domain_ref={
                "draft_id": draft_id,
                "revision": expected + 1,
                "status": "saved",
                "private_pack_version_id": version.id,
                "pack_id": version.pack_id,
                "name": version.name,
                "version": version.version,
            },
        )
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary=f"Saved private Analysis Pack {version.name} version {version.version}.",
            data={
                "draft_id": draft_id,
                "private_pack_version_id": version.id,
                "pack_id": version.pack_id,
                "name": version.name,
                "version": version.version,
            },
            artifact_links=[
                {
                    "label": version.name,
                    "url": "/pack?tab=library",
                    "kind": "private_pack",
                }
            ],
        )


class PackDraftAbandonHandler(_PackDraftHandler):
    capability_id = PACK_DRAFT_ABANDON_CAPABILITY.id
    input_schema = PACK_DRAFT_ABANDON_CAPABILITY.input_schema
    output_type = PACK_DRAFT_ABANDON_CAPABILITY.output_type
    task_types = tuple(PACK_DRAFT_ABANDON_CAPABILITY.task_types)

    async def __call__(self, capability, request, context, services) -> CapabilityObservation:
        draft_service, state_repo = self._services(services)
        claim_id, token, call_key = self._claim(context, request)
        expected = int(request.arguments["expected_revision"])
        draft_id = int(request.arguments["draft_id"])
        await self._require_conversation_draft(
            draft_service,
            draft_id=draft_id,
            owner_user_id=context.user_id,
            conversation_id=context.conversation_id,
        )
        replay = await state_repo.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            capability_id=capability.id,
            authority_hint={
                "kind": "pack_revision",
                "draft_id": draft_id,
                "expected_revision": expected,
                "target_revision": expected + 1,
            },
        )
        if replay is not None:
            removed = replay.effect.remove_task_ref_id
            if not isinstance(removed, str) or not removed.startswith("pack:"):
                raise RuntimeError("Abandoned Pack receipt is missing its draft authority")
            draft = await draft_service.get_draft(int(removed.removeprefix("pack:")), context.user_id)
            return self._observation(
                capability,
                draft,
                summary=f"Abandoned Pack draft {draft.name}.",
            )
        try:
            draft = await draft_service.abandon(draft_id, context.user_id, expected)
        except DraftServiceError as exc:
            await state_repo.abort_effect(
                claim_id=claim_id,
                lease_token=token,
                call_key=call_key,
                capability_id=capability.id,
                reason=str(exc),
            )
            raise
        await state_repo.record_effect(
            claim_id=claim_id,
            lease_token=token,
            effect=ConversationTaskEffect(
                call_key=call_key,
                capability_id=capability.id,
                remove_task_ref_id=f"pack:{draft_id}",
            ),
            domain_ref={
                "draft_id": draft.id,
                "revision": draft.revision,
                "status": draft.status,
            },
        )
        return self._observation(
            capability, draft, summary=f"Abandoned Pack draft {draft.name}."
        )


class RegisteredEvidenceHandler:
    """Normalize one server-owned read adapter into the shared observation."""

    def __init__(self, capability: CapabilityDefinition) -> None:
        self.capability_id = capability.id
        self.input_schema = capability.input_schema
        self.output_type = capability.output_type
        self.task_types = tuple(capability.task_types)

    async def __call__(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        services: CapabilityHandlerServices,
    ) -> CapabilityObservation:
        adapter = services.evidence_handlers.get(capability.id)
        if adapter is None:
            raise RuntimeError(f"Read adapter is not configured for {capability.id}")
        try:
            result = await adapter(dict(request.arguments), context)
        except Exception:
            return CapabilityObservation(
                capability_id=capability.id,
                status="failed",
                output_type=capability.output_type,
                summary="The evidence source could not answer this request.",
                error_message="The evidence provider is unavailable for this request.",
                retryable=True,
                safe_details={"evidence_count": 0},
            )
        raw_items = getattr(result, "items", result)
        items = raw_items if isinstance(raw_items, list) else []
        typed_items: list[dict[str, Any]] = []
        for item in items:
            serialized = _serialized_evidence_item(item)
            if serialized is not None:
                typed_items.append(serialized)
        result_status = str(getattr(result, "status", "succeeded"))
        error_message = getattr(result, "error_message", None)
        answer_draft = getattr(result, "answer_draft", None)
        if result_status not in {"succeeded", "success", "completed"}:
            safe_error = (
                _safe_text(error_message, limit=500)
                if error_message
                else "The evidence source returned no usable evidence."
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="failed",
                output_type=capability.output_type,
                summary="The evidence source could not answer this request.",
                error_message=safe_error,
                retryable=True,
                safe_details={"evidence_count": len(typed_items)},
            )
        data: dict[str, Any] = {"items": typed_items}
        if isinstance(answer_draft, str) and answer_draft.strip():
            data["answer_draft"] = _safe_text(answer_draft, limit=12_000)
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary=(
                f"Retrieved {len(typed_items)} evidence item(s)."
                if typed_items
                else "No evidence matched the authorized scope."
            ),
            data=data,
            evidence_refs=[_evidence_reference(item) for item in typed_items],
            safe_details={"evidence_count": len(typed_items)},
        )


def _serialized_evidence_item(item: Any) -> dict[str, Any] | None:
    if hasattr(item, "model_dump"):
        value = item.model_dump(mode="json")
    elif isinstance(item, dict):
        value = dict(item)
    else:
        return None
    if not isinstance(value, dict):
        return None
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    safe_metadata: dict[str, Any] = {}
    for key, raw in metadata.items():
        if key not in _SAFE_EVIDENCE_METADATA_KEYS:
            continue
        if key == "filename" and isinstance(raw, str):
            raw = raw.replace("\\", "/").rsplit("/", 1)[-1]
        safe = _safe_metadata_value(raw)
        if safe is not None:
            safe_metadata[key] = safe
    result = {
        "id": _safe_text(value.get("id"), limit=500),
        "capability_id": _safe_text(value.get("capability_id"), limit=200),
        "source_type": _safe_text(value.get("source_type"), limit=200),
        "title": _safe_text(value.get("title"), limit=500),
        "summary": _safe_text(value.get("summary"), limit=4_000),
        "uri": _safe_uri(value.get("uri")),
        "score": value.get("score") if isinstance(value.get("score"), (int, float)) else None,
        "observed_at": _safe_text(value.get("observed_at"), limit=100),
        "metadata": safe_metadata,
    }
    return {key: raw for key, raw in result.items() if raw is not None}


def _safe_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    text = redact_credentials(str(value))
    text = _SECRET_TOKEN_RE.sub("<redacted>", text)
    return text[:limit]


def _safe_metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value, limit=500)
    if isinstance(value, list):
        return [
            safe
            for item in value[:20]
            if (safe := _safe_metadata_value(item)) is not None
        ]
    return None


def _safe_uri(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = urlsplit(value)
    if not parsed.scheme and not parsed.netloc:
        if parsed.path.startswith(_SAFE_URI_PREFIXES):
            return parsed.path[:1_000]
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:1_000]


def _evidence_reference(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    ref = {
        "evidence_id": item.get("id"),
        "source_type": item.get("source_type"),
        "project_id": metadata.get("project_id"),
        "document_id": metadata.get("document_id"),
        "chunk_index": metadata.get("chunk_index"),
        "runtime_record_id": metadata.get("record_ref") or metadata.get("execution_ref"),
        "uri": item.get("uri"),
        "observed_at": item.get("observed_at"),
        "score": item.get("score"),
    }
    return {key: value for key, value in ref.items() if value is not None}


def migrated_capability_handlers() -> tuple[object, ...]:
    return (
        DocumentationGenerationHandler(),
        DocumentationGenerationStatusHandler(),
        DocumentationPackCatalogHandler(),
        DocumentationUpdateProposalHandler(),
        DocumentationUpdateHandler(),
        DocumentationRevertHandler(),
        WorkflowCancellationHandler(),
        ActiveProjectWorkflowStatusHandler(),
        SourceOptionsHandler(),
        AttachGitSourceHandler(),
        AttachZipSourceHandler(),
        TriggerScanHandler(),
        ProjectStatusHandler(),
        PublishIndexHandler(),
        PackDraftCreateHandler(),
        PackDraftReadHandler(),
        PackDraftMutateHandler(),
        PackDraftValidateHandler(),
        PackDraftSaveHandler(),
        PackDraftAbandonHandler(),
        ProjectOnboardingHandler(),
        OnboardingResumeHandler(),
        *(
            RegisteredEvidenceHandler(capability)
            for capability in (
                DOCUMENT_SEARCH_CAPABILITY,
                DOCUMENT_READ_CAPABILITY,
                DOCUMENT_LIST_CAPABILITY,
                METADATA_QUERY_CAPABILITY,
                STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
                LOG_SEARCH_EVIDENCE_CAPABILITY,
                CONVERSATION_SUMMARY_FACTS_CAPABILITY,
            )
        ),
    )
