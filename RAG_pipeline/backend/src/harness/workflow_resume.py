"""Authoritative workflow-resume coordinators used by the chat API.

These functions preserve durable workflow interrupt behavior without coupling
the API to the retired natural-language chat router.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.harness import (
    HarnessWorkflowRepository,
    HarnessWorkflowService,
    default_capability_registry,
)
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import ManifestScope, build_runtime_capability_manifest
from src.harness.models import (
    ATTACH_GIT_SOURCE_CAPABILITY,
    ATTACH_ZIP_SOURCE_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    ActivityEvent,
    ActivityEventStatus,
    WorkflowInterruptType,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowRunUpdate,
)
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.providers.mcp_projects import McpProjectsProvider
from src.harness.registry import CapabilityRegistry, base_capability_registry
from src.harness.runtime import RuntimeTurnRequest, default_langgraph_runtime_provider
from src.harness.workflow_ledger import WorkflowObservationSource
from src.harness.workflows import (
    DocumentationUpdateRequest,
    OnboardingRequest,
    default_documentation_update_workflow,
    default_project_onboarding_workflow,
    normalize_project_platform,
)


_DOCUMENTATION_GENERATION_CAPABILITY_ID = DOCUMENTATION_GENERATION_CAPABILITY.id
_DOCUMENT_UPDATE_CAPABILITY_ID = DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id
_DOCUMENTATION_PACK_WORKFLOW_KEY = "documentation.generate"


async def resume_documentation_generation_workflow(
    *,
    run: WorkflowRun,
    project_id: str,
    db: AsyncSession,
    user_id: int,
    project_scope: list[str] | None,
    user_permissions: set[str] | None,
    access_token: str | None,
    provider: McpDocumentationProvider | None = None,
) -> tuple[WorkflowRun, list[ActivityEvent]]:
    """Continue one owner-authorized documentation workflow."""

    if project_scope is not None and project_id not in project_scope:
        raise ValueError("Project access required")
    registry = await _project_execution_registry(
        db, project_id, _DOCUMENTATION_PACK_WORKFLOW_KEY
    )
    docs_provider = provider or McpDocumentationProvider()
    executor = HarnessCapabilityExecutor(registry, docs_provider=docs_provider)
    context = CapabilityExecutionContext(
        user_id=user_id,
        conversation_id=run.conversation_id,
        project_scope=[project_id],
        permissions=user_permissions,
        access_token=access_token,
    )
    request = RuntimeTurnRequest(
        message=run.input_summary or "Generate documentation",
        manifest=build_runtime_capability_manifest(
            registry,
            scope=ManifestScope(permissions=user_permissions),
        ),
        planned_capability_id=_DOCUMENTATION_GENERATION_CAPABILITY_ID,
        arguments={},
        workflow_run_id=run.id,
        thread_id=run.runtime_thread_id or f"harness-run-{run.id}",
    )
    runtime = default_langgraph_runtime_provider()
    runtime_result = await runtime.resume_turn(
        request,
        response={"project_id": project_id},
        executor=executor,
        context=context,
    )
    # Nexus owns durable interrupt state. Reconstruct the same run when a
    # process-local LangGraph checkpoint disappeared during a restart.
    if runtime_result.status == "failed" and runtime_result.current_node == "runtime_error":
        runtime_result = await runtime.run_turn(
            request.model_copy(update={"arguments": {"project_id": project_id}}),
            executor=executor,
            context=context,
        )

    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    activities = await _record_runtime_activities(
        service=service,
        run=run,
        activity=runtime_result.activity,
        default_event_type="runtime.event",
        default_label="Runtime activity",
        tool_call_id=runtime_result.runtime_run_id,
    )
    observation = runtime_result.observation
    repo = HarnessWorkflowRepository(db)
    if observation is not None and observation.succeeded:
        updated = await repo.update_run(
            None,
            run.id,
            WorkflowRunUpdate(
                project_id=project_id,
                status=WorkflowRunStatus.TRIGGERED,
                output_summary=observation.summary,
                artifact_links=observation.artifact_links,
                runtime_provider_id=runtime_result.runtime_provider,
                runtime_thread_id=runtime_result.thread_id,
                runtime_run_id=runtime_result.runtime_run_id,
                current_node=runtime_result.current_node,
                artifact_receipts=_with_pack_governance_receipt(
                    run, observation.artifact_links
                ),
                runtime_metadata=runtime_result.runtime_metadata,
            ),
            owner_user_id=user_id,
        )
        external_run_id = observation.data.get("external_run_id")
        if external_run_id is not None:
            await repo.bind_child_receipt(
                [project_id],
                workflow_run_id=updated.id,
                provider_id="mcp.docs",
                child_kind="documentation_generation",
                external_run_id=external_run_id,
                project_id=project_id,
                provider_status=str(
                    observation.data.get("status") or "generation_triggered"
                ),
                source=WorkflowObservationSource.TRIGGER,
                owner_user_id=user_id,
            )
            updated = await repo.get_run(
                [project_id], updated.id, owner_user_id=user_id
            )
        activities.append(
            await service.record_activity(
                updated,
                event_type="workflow.resumed",
                label="Documentation workflow resumed",
                status=ActivityEventStatus.SUCCEEDED,
                summary=f"Documentation generation started for `{project_id}`.",
                safe_details={"project_id": project_id},
                runtime_node=runtime_result.current_node,
            )
        )
        return updated, activities

    error = (
        observation.error_message
        if observation is not None
        else runtime_result.final_summary
    ) or "Documentation generation could not continue."
    updated = await repo.update_run(
        None,
        run.id,
        WorkflowRunUpdate(
            project_id=project_id,
            status=WorkflowRunStatus.FAILED,
            error_message=error,
            runtime_provider_id=runtime_result.runtime_provider,
            runtime_thread_id=runtime_result.thread_id,
            runtime_run_id=runtime_result.runtime_run_id,
            current_node=runtime_result.current_node,
            runtime_metadata=runtime_result.runtime_metadata,
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
        owner_user_id=user_id,
    )
    return updated, activities


async def resume_project_onboarding_workflow(
    *,
    run: WorkflowRun,
    response_value: str,
    required_fields: list[str] | None,
    db: AsyncSession,
    user_id: int,
    project_scope: list[str] | None,
    user_permissions: set[str] | None,
    access_token: str | None,
) -> tuple[WorkflowRun, list[ActivityEvent]]:
    """Continue a durable project-onboarding interrupt."""

    metadata_arguments = run.runtime_metadata.get("arguments")
    arguments = metadata_arguments if isinstance(metadata_arguments, dict) else {}
    project_id = str(arguments.get("project_id") or run.project_id)
    platform = str(arguments.get("platform") or "generic")
    source_type = arguments.get("source_type")
    if source_type not in {"git", "zip"}:
        source_type = None
    plugin_pack_id = arguments.get("plugin_pack_id")
    if (
        not isinstance(plugin_pack_id, int)
        or isinstance(plugin_pack_id, bool)
        or plugin_pack_id < 1
    ):
        plugin_pack_id = None
    registry = await _project_execution_registry(
        db, project_id, _DOCUMENTATION_PACK_WORKFLOW_KEY
    )
    context = CapabilityExecutionContext(
        user_id=user_id,
        conversation_id=run.conversation_id,
        project_scope=list(dict.fromkeys([*(project_scope or []), project_id])),
        permissions=user_permissions,
        approvals={ATTACH_GIT_SOURCE_CAPABILITY.id, ATTACH_ZIP_SOURCE_CAPABILITY.id},
        access_token=access_token,
    )
    docs_provider = McpDocumentationProvider()
    executor = HarnessCapabilityExecutor(
        registry,
        docs_provider=docs_provider,
        projects_provider=McpProjectsProvider(),
    )
    request = OnboardingRequest(
        workflow_run_id=run.id,
        project_id=project_id,
        platform=platform,
        source_type=source_type,
        selected_pack_ids=selected_pack_ids,
        plugin_pack_id=plugin_pack_id,
        thread_id=run.runtime_thread_id,
        proposal_id=(str(arguments["proposal_id"]) if arguments.get("proposal_id") else None),
        patch_plan_hash=(str(arguments["patch_plan_hash"]) if arguments.get("patch_plan_hash") else None),
        base_knowledge_revision_id=(str(arguments["base_knowledge_revision_id"]) if arguments.get("base_knowledge_revision_id") else None),
        compact_diff=(str(arguments["compact_diff"]) if arguments.get("compact_diff") else None),
        changed_scope=[str(item) for item in arguments.get("changed_scope", [])],
        assertion_kind=str(arguments.get("assertion_kind") or "source_backed"),
        expires_at=(str(arguments["expires_at"]) if arguments.get("expires_at") else None),
    )
    fields = set(required_fields or [])
    response: dict[str, Any] = {"platform": platform}
    if "upload_receipt" in fields:
        response.update({"source_type": "zip", "upload_receipt": response_value})
    elif "source_type" in fields:
        response["source_type"] = response_value.strip().lower()
    elif "platform" in fields:
        response["platform"] = normalize_project_platform(response_value) or platform
    else:
        response.update({"source_type": "git", "repo_url": response_value})

    workflow = default_project_onboarding_workflow()
    try:
        result = await workflow.resume(
            request,
            response=response,
            executor=executor,
            context=context,
        )
    except Exception:
        request_updates: dict[str, Any] = {}
        if "upload_receipt" in fields:
            request_updates["upload_receipt"] = response_value
        elif "source_type" not in fields:
            request_updates["repo_url"] = response_value
        result = await workflow.run(
            request.model_copy(update=request_updates),
            executor=executor,
            context=context,
        )

    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    activities = await _record_runtime_activities(
        service=service,
        run=run,
        activity=result.activity,
        default_event_type="onboarding.event",
        default_label="Project onboarding",
    )
    if result.status == "interrupted":
        updated = await service.mark_interrupted(
            run,
            output_summary="Additional project source information is required.",
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=result.thread_id,
            current_node=result.current_node,
            runtime_metadata={
                "graph": "project_onboarding",
                "interrupt": {
                    "langgraph_interrupt_id": result.interrupt_id,
                    "missing_slots": result.interrupt_fields,
                },
                "arguments": {
                    "project_id": project_id,
                    "platform": platform,
                    "source_type": source_type,
                    "selected_pack_ids": selected_pack_ids,
                    "plugin_pack_id": plugin_pack_id,
                },
            },
            interrupt_type=WorkflowInterruptType.CLARIFICATION,
            prompt=result.interrupt_prompt or "Additional source information is required.",
            options=_onboarding_interrupt_options(result.interrupt_fields),
            scope_snapshot={
                "project_id": project_id,
                "platform": platform,
                "source_type": source_type,
                "required_fields": result.interrupt_fields,
                "available_projects": list(
                    dict.fromkeys([*(project_scope or []), project_id])
                ),
            },
            policy_snapshot={"capability": "project onboarding"},
            normalized_arguments={
                "project_id": project_id,
                "platform": platform,
                "selected_pack_ids": selected_pack_ids,
                "plugin_pack_id": plugin_pack_id,
                "required_fields": result.interrupt_fields,
            },
        )
        return updated, activities

    status = (
        WorkflowRunStatus.TRIGGERED
        if result.status != "failed"
        else WorkflowRunStatus.FAILED
    )
    error = next(
        (
            observation.error_message or observation.summary
            for observation in reversed(result.observations)
            if not observation.succeeded
        ),
        None,
    )
    source_receipts = [
        {
            "kind": "zip_source",
            "filename": observation.data.get("filename"),
            "size_bytes": observation.data.get("size_bytes"),
            "sha256": observation.data.get("sha256"),
        }
        for observation in result.observations
        if observation.capability_id == ATTACH_ZIP_SOURCE_CAPABILITY.id
        and observation.succeeded
    ]
    if plugin_pack_id is not None and result.status != "failed":
        await docs_provider.set_project_documentation_pack(
            project_id=project_id,
            plugin_pack_id=plugin_pack_id,
            access_token=access_token,
        )
    updated = await HarnessWorkflowRepository(db).update_run(
        None,
        run.id,
        WorkflowRunUpdate(
            status=status,
            output_summary=(
                "Project onboarding resumed."
                if status == WorkflowRunStatus.TRIGGERED
                else None
            ),
            error_message=error,
            artifact_links=result.artifact_links,
            artifact_receipts=_with_pack_governance_receipt(
                run, [*result.artifact_links, *source_receipts]
            ),
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=result.thread_id,
            current_node=result.current_node,
            runtime_metadata={"graph": "project_onboarding"},
            finished_at=(
                datetime.datetime.now(datetime.UTC)
                if status == WorkflowRunStatus.FAILED
                else None
            ),
        ),
        owner_user_id=user_id,
    )
    return updated, activities


async def resume_documentation_update_workflow(
    *,
    run: WorkflowRun,
    approved: bool,
    db: AsyncSession,
    user_id: int,
    project_scope: list[str] | None,
    user_permissions: set[str] | None,
    access_token: str | None,
    arguments: dict[str, Any],
) -> tuple[WorkflowRun, list[ActivityEvent]]:
    """Continue a durable documentation-update approval interrupt."""

    project_id = str(arguments.get("project_id") or "")
    if project_scope is not None and project_id not in project_scope:
        raise ValueError("Project access required")
    registry = default_capability_registry()
    request = DocumentationUpdateRequest(
        workflow_run_id=run.id,
        project_id=project_id,
        target_doc=str(arguments.get("target_doc") or ""),
        evidence_refs=[str(item) for item in arguments.get("evidence_refs", [])],
        evidence_text=str(arguments.get("evidence_text") or ""),
        expected_base_sha=str(arguments.get("expected_base_sha") or ""),
        section=(str(arguments["section"]) if arguments.get("section") else None),
        thread_id=run.runtime_thread_id,
    )
    context = CapabilityExecutionContext(
        user_id=user_id,
        conversation_id=run.conversation_id,
        project_scope=[project_id],
        permissions=user_permissions,
        access_token=access_token,
    )
    executor = HarnessCapabilityExecutor(
        registry, docs_provider=McpDocumentationProvider()
    )
    workflow = default_documentation_update_workflow()
    try:
        runtime_result = await workflow.resume(
            request,
            approved=approved,
            executor=executor,
            context=context,
        )
    except Exception:
        fallback_context = context.model_copy(
            update={
                "approvals": {_DOCUMENT_UPDATE_CAPABILITY_ID} if approved else set()
            }
        )
        runtime_result = await workflow.run(
            request,
            executor=executor,
            context=fallback_context if approved else context,
        )

    service = _workflow_service(db)
    activities: list[ActivityEvent] = []
    for item in runtime_result.activity:
        activity_status = _activity_status(item.get("status"))
        label = (
            "Documentation update failed"
            if activity_status is ActivityEventStatus.FAILED
            else str(item.get("label") or "Documentation update")
        )
        activities.append(
            await service.record_activity(
                run,
                event_type=str(item.get("event_type") or "runtime.event"),
                label=label,
                status=activity_status,
                summary=item.get("summary"),
                safe_details=dict(item.get("safe_details") or {}),
                runtime_node=item.get("runtime_node"),
            )
        )
    repo = HarnessWorkflowRepository(db)
    if not approved or runtime_result.status == "cancelled":
        updated = await repo.update_run(
            None,
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.CANCELLED,
                cancellation_reason="Documentation update was not approved.",
                cancelled_at=datetime.datetime.now(datetime.UTC),
                cancelled_by_user_id=user_id,
                finished_at=datetime.datetime.now(datetime.UTC),
                runtime_provider_id="langgraph.nexus",
                runtime_thread_id=runtime_result.thread_id,
                current_node=runtime_result.current_node,
                runtime_metadata={"graph": "documentation_update"},
            ),
            owner_user_id=user_id,
        )
        return updated, activities
    observation = runtime_result.observation
    if observation is None or not observation.succeeded:
        error = (
            observation.error_message if observation is not None else None
        ) or "Documentation update failed."
        updated = await repo.update_run(
            None,
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.FAILED,
                error_message=error,
                finished_at=datetime.datetime.now(datetime.UTC),
                runtime_provider_id="langgraph.nexus",
                runtime_thread_id=runtime_result.thread_id,
                current_node=runtime_result.current_node,
                runtime_metadata={"graph": "documentation_update"},
            ),
            owner_user_id=user_id,
        )
        return updated, activities
    updated = await repo.update_run(
        None,
        run.id,
        WorkflowRunUpdate(
            status=WorkflowRunStatus.SUCCEEDED,
            output_summary=observation.summary,
            artifact_links=observation.artifact_links,
            artifact_receipts=_with_pack_governance_receipt(
                run, observation.artifact_links
            ),
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=runtime_result.thread_id,
            current_node=runtime_result.current_node,
            runtime_metadata={
                "graph": "documentation_update",
                "receipt": observation.data,
            },
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
        owner_user_id=user_id,
    )
    return updated, activities


def _workflow_service(db: AsyncSession) -> HarnessWorkflowService:
    return HarnessWorkflowService(
        HarnessWorkflowRepository(db),
        default_capability_registry(),
    )


async def _project_execution_registry(
    db: AsyncSession,
    project_id: str,
    workflow_key: str,
) -> CapabilityRegistry:
    # Project generation is controlled solely by MCP's persisted
    # ``plugin_pack_id``. The RAG runtime keeps its fixture-driven capability
    # allowlist, but no longer compiles a second, project-selectable pack stack.
    _ = (db, project_id, workflow_key)
    return default_capability_registry()


async def _record_runtime_activities(
    *,
    service: HarnessWorkflowService,
    run: WorkflowRun,
    activity: list[dict[str, Any]],
    default_event_type: str,
    default_label: str,
    tool_call_id: str | None = None,
) -> list[ActivityEvent]:
    result: list[ActivityEvent] = []
    for item in activity:
        result.append(
            await service.record_activity(
                run,
                event_type=str(item.get("event_type") or default_event_type),
                label=str(item.get("label") or default_label),
                status=_activity_status(item.get("status")),
                summary=item.get("summary"),
                safe_details=dict(item.get("safe_details") or {}),
                runtime_node=item.get("runtime_node"),
                tool_call_id=tool_call_id,
            )
        )
    return result


def _onboarding_interrupt_options(
    required_fields: list[str],
) -> list[dict[str, str]]:
    if "source_type" not in required_fields:
        return []
    return [
        {"label": "Git repository", "value": "git"},
        {"label": "ZIP upload", "value": "zip"},
    ]


def _with_pack_governance_receipt(
    run: WorkflowRun, receipts: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    result = list(receipts or [])
    receipt = run.runtime_metadata.get("pack_governance")
    if isinstance(receipt, dict) and not any(
        item.get("kind") == "pack_governance" for item in result
    ):
        result.append({"kind": "pack_governance", **receipt})
    return result


def _activity_status(status: Any) -> ActivityEventStatus:
    value = str(status or "").lower()
    if value in {"succeeded", "success", "completed", "done", "triggered"}:
        return ActivityEventStatus.SUCCEEDED
    if value in {"failed", "error", "denied"}:
        return ActivityEventStatus.FAILED
    if value in {"skipped", "interrupted"}:
        return ActivityEventStatus.SKIPPED
    return ActivityEventStatus.RUNNING
