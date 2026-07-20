"""API routes for Nexus harness capabilities and workflow receipts."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.api.dependencies import CurrentUser, DBSession
from src.harness import HarnessRunNotFoundError, HarnessWorkflowRepository, default_capability_registry
from src.harness.registry import base_capability_registry
from src.harness.packs import PackSnapshot, PackView
from src.harness.packs.runtime import build_pack_catalog, resolve_active_packs
from src.harness.packs.project_service import ProjectPackService
from src.harness.manifest import (
    ManifestScope,
    RuntimeCapabilityManifest,
    build_runtime_capability_manifest,
)
from src.harness.models import (
    ActivityEvent,
    CapabilityDefinition,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowInterrupt,
    WorkflowResumeRequest,
    WorkflowCancelRequest,
    WorkflowActionResponse,
    WorkflowInterruptType,
)
from src.harness.repository import HarnessInterruptUnavailableError, HarnessWorkflowConflictError
from src.harness.service import HarnessWorkflowService
from src.harness.connectors import ConnectorRepository
from src.harness.connectors.capabilities import build_request_capability_registry
from src.harness.evidence_store import EvidenceSnapshot, HarnessEvidenceRepository
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.reconciliation import DocumentationWorkflowReconciler
from src.harness.workflow_control import (
    WorkflowCancellationController,
    WorkflowCancellationOutcome,
)
from src.harness.workflow_resume import (
    resume_documentation_generation_workflow,
    resume_documentation_update_workflow,
    resume_project_onboarding_workflow,
)
from src.rbac.permissions import get_user_permissions, get_user_project_ids
from src.runtime.models import RuntimeSourceTypeInfo
from src.runtime.source_registry import all_source_providers

router = APIRouter(prefix="/harness", tags=["harness"])


class CapabilityListResponse(BaseModel):
    capabilities: list[CapabilityDefinition]
    total: int


class WorkflowRunListResponse(BaseModel):
    runs: list[WorkflowRun]
    total: int


class WorkflowRunDetailResponse(BaseModel):
    run: WorkflowRun
    activities: list[ActivityEvent]
    pending_interrupts: list[WorkflowInterrupt] = Field(default_factory=list)
    evidence: list[EvidenceSnapshot] = Field(default_factory=list)


class HarnessConnectorInfo(BaseModel):
    provider_key: str
    provider_label: str
    connector_type: str
    auth_mode: str
    credential_schema: list[dict]
    capability_ids: list[str]
    risk_tier: str
    enabled: bool


class HarnessConnectorListResponse(BaseModel):
    connectors: list[HarnessConnectorInfo]
    total: int


class HarnessPackListResponse(BaseModel):
    packs: list[PackView]
    active: PackSnapshot
    total: int


@router.get("/capabilities", response_model=CapabilityListResponse)
async def list_capabilities(
    user: CurrentUser,
    enabled_only: bool = True,
) -> CapabilityListResponse:
    """List registered harness capabilities.

    Capability metadata is safe to expose to authenticated users: it describes
    available actions and risk tiers, not credentials or raw provider payloads.
    """
    _ = user
    capabilities = default_capability_registry().list(enabled_only=enabled_only)
    return CapabilityListResponse(capabilities=capabilities, total=len(capabilities))


@router.get("/packs", response_model=HarnessPackListResponse)
async def list_packs(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    workflow_key: str = "default",
) -> HarnessPackListResponse:
    """List safe Pack definitions and a selected project's active snapshot when scoped."""
    registry = base_capability_registry()
    if project_id is not None:
        scope = await get_user_project_ids(user, db)
        if scope is not None and project_id not in scope:
            raise HTTPException(status_code=404, detail="Project not found")
        service = ProjectPackService(db, registry)
        catalog = await service.catalog(project_id)
        try:
            active = await service.current_activation(project_id, workflow_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if active is not None:
            return HarnessPackListResponse(
                packs=list(catalog.views()), active=active.snapshot, total=len(catalog.views())
            )
    else:
        catalog = build_pack_catalog(registry)
    resolved = resolve_active_packs(registry)
    views = list(catalog.views())
    return HarnessPackListResponse(
        packs=views,
        active=resolved.snapshot,
        total=len(views),
    )


@router.get("/manifest", response_model=RuntimeCapabilityManifest)
async def get_runtime_manifest(
    user: CurrentUser,
    db: DBSession,
    include_unavailable: bool = False,
) -> RuntimeCapabilityManifest:
    """Return the runtime-safe capability manifest for agent orchestration."""
    project_ids = await get_user_project_ids(user, db)
    registry = await build_request_capability_registry(
        ConnectorRepository(db), project_ids=project_ids
    )
    return build_runtime_capability_manifest(
        registry,
        scope=ManifestScope(
            project_ids=project_ids,
            permissions=get_user_permissions(user),
            include_unavailable=include_unavailable,
        ),
    )


@router.get("/connector-types", response_model=HarnessConnectorListResponse)
async def list_connectors(user: CurrentUser) -> HarnessConnectorListResponse:
    """List connector types available to the harness.

    This reuses the existing runtime source provider contract instead of
    creating a second credential/configuration system. Only safe schema metadata
    is returned; credential values are never exposed.
    """
    _ = user
    connectors = [
        HarnessConnectorInfo(
            provider_key=provider.provider_key(),
            provider_label=provider.provider_label(),
            connector_type="runtime_source",
            auth_mode="credential_fields",
            credential_schema=[
                dict(field)
                for field in RuntimeSourceTypeInfo(
                    provider_key=provider.provider_key(),
                    provider_label=provider.provider_label(),
                    credential_schema=provider.credential_schema(),
                ).credential_schema
            ],
            capability_ids=[STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id],
            risk_tier=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.risk_tier.value,
            enabled=True,
        )
        for provider in all_source_providers()
    ]
    return HarnessConnectorListResponse(connectors=connectors, total=len(connectors))


@router.get("/workflows", response_model=WorkflowRunListResponse)
async def list_workflow_runs(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    conversation_id: int | None = None,
    project_id: str | None = None,
    capability_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> WorkflowRunListResponse:
    scope = await get_user_project_ids(user, db)
    scoped_project_ids = _intersect_scope(scope, project_id)
    workflow_status = _parse_status(status)
    repo = HarnessWorkflowRepository(db)
    runs = await repo.list_runs(
        scoped_project_ids,
        conversation_id=conversation_id,
        project_id=project_id,
        capability_id=capability_id,
        status=workflow_status,
        owner_user_id=user.id,
        limit=limit,
    )
    reconciler = DocumentationWorkflowReconciler(repo, McpDocumentationProvider())
    reconciled_runs: list[WorkflowRun] = []
    for run in runs:
        if run.capability_id == "mcp.docs.generate_project_documentation" and (
            run.status in {
                WorkflowRunStatus.RUNNING,
                WorkflowRunStatus.TRIGGERED,
                WorkflowRunStatus.INDETERMINATE,
            }
            or any(child.status not in {"succeeded", "failed", "cancelled"} for child in run.children)
        ):
            run = (
                await reconciler.reconcile(
                    run,
                    access_token=request.cookies.get("access_token"),
                )
            ).run
        reconciled_runs.append(run)
    runs = reconciled_runs
    return WorkflowRunListResponse(runs=runs, total=len(runs))


@router.get("/workflows/{run_id}", response_model=WorkflowRunDetailResponse)
async def get_workflow_run(
    run_id: int,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> WorkflowRunDetailResponse:
    scope = await get_user_project_ids(user, db)
    repo = HarnessWorkflowRepository(db)
    try:
        run = await repo.get_run(scope, run_id, owner_user_id=user.id)
        if run.capability_id == "mcp.docs.generate_project_documentation" and (
            run.status in {
                WorkflowRunStatus.RUNNING,
                WorkflowRunStatus.TRIGGERED,
                WorkflowRunStatus.INDETERMINATE,
            }
            or any(child.status not in {"succeeded", "failed", "cancelled"} for child in run.children)
        ):
            run = (
                await DocumentationWorkflowReconciler(
                    repo, McpDocumentationProvider()
                ).reconcile(
                    run,
                    access_token=request.cookies.get("access_token"),
                )
            ).run
        activity_scope = (
            None
            if scope is None
            else list(set(scope) | ({run.project_id} if run.project_id is not None else set()))
        )
        activities = await repo.list_activity_events(activity_scope, run_id)
        interrupts = await repo.list_pending_interrupts_for_owner(run_id, user.id)
        evidence = await HarnessEvidenceRepository(db).list_collected(run_id)
    except HarnessRunNotFoundError:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return WorkflowRunDetailResponse(
        run=run,
        activities=activities,
        pending_interrupts=interrupts,
        evidence=evidence,
    )


@router.post("/workflows/{run_id}/resume", response_model=WorkflowActionResponse)
async def resume_workflow_run(
    run_id: int,
    payload: WorkflowResumeRequest,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> WorkflowActionResponse:
    repo = HarnessWorkflowRepository(db)
    service = HarnessWorkflowService(repo, default_capability_registry())
    try:
        original_run = await repo.get_run(
            await get_user_project_ids(user, db),
            run_id,
            owner_user_id=user.id,
        )
        pending_interrupt = await repo.get_interrupt_for_owner(
            payload.interrupt_id, run_id, user.id
        )
        required_fields = pending_interrupt.scope_snapshot.get("required_fields")
        required_field_names = (
            [str(value) for value in required_fields if isinstance(value, str)]
            if isinstance(required_fields, list)
            else []
        )
        audit_response = (
            "ZIP source uploaded"
            if "upload_receipt" in required_field_names
            else payload.response
        )
        run = await service.resume(
            run_id=run_id,
            owner_user_id=user.id,
            interrupt_id=payload.interrupt_id,
            expected_version=payload.expected_version,
            response=audit_response,
        )
        if (
            original_run.intent_mode == "generate_docs"
            and pending_interrupt.interrupt_type == WorkflowInterruptType.CLARIFICATION
            and isinstance(
                pending_interrupt.scope_snapshot.get("available_projects"), list
            )
        ):
            run, _ = await resume_documentation_generation_workflow(
                run=run,
                project_id=payload.response.strip(),
                db=db,
                user_id=user.id,
                project_scope=await get_user_project_ids(user, db),
                user_permissions=get_user_permissions(user),
                access_token=request.cookies.get("access_token"),
            )
        elif (
            original_run.intent_mode == "project_onboarding"
            and pending_interrupt.interrupt_type == WorkflowInterruptType.CLARIFICATION
        ):
            run, _ = await resume_project_onboarding_workflow(
                run=run,
                response_value=payload.response.strip(),
                required_fields=required_field_names,
                db=db,
                user_id=user.id,
                project_scope=await get_user_project_ids(user, db),
                user_permissions=get_user_permissions(user),
                access_token=request.cookies.get("access_token"),
            )
        elif (
            original_run.intent_mode == "update_docs_from_evidence"
            and pending_interrupt.interrupt_type == WorkflowInterruptType.APPROVAL
        ):
            arguments = pending_interrupt.scope_snapshot.get("execution_arguments")
            if not isinstance(arguments, dict) or _argument_hash(arguments) != pending_interrupt.argument_hash:
                raise HarnessWorkflowConflictError("Workflow arguments changed while awaiting approval")
            normalized = payload.response.strip().lower()
            if normalized not in {"approve", "approved", "yes", "decline", "declined", "no"}:
                raise ValueError("Choose approve or decline")
            run, _ = await resume_documentation_update_workflow(
                run=run,
                approved=normalized in {"approve", "approved", "yes"},
                db=db,
                user_id=user.id,
                project_scope=await get_user_project_ids(user, db),
                user_permissions=get_user_permissions(user),
                access_token=request.cookies.get("access_token"),
                arguments=arguments,
            )
    except HarnessRunNotFoundError:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Workflow run not found")
    except (HarnessInterruptUnavailableError, HarnessWorkflowConflictError):
        await db.rollback()
        raise HTTPException(status_code=409, detail="Workflow continuation is no longer available")
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc))
    return WorkflowActionResponse(
        run=run,
        message=_workflow_action_message(run),
    )


@router.post("/workflows/{run_id}/cancel", response_model=WorkflowActionResponse)
async def cancel_workflow_run(
    run_id: int,
    payload: WorkflowCancelRequest,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> WorkflowActionResponse:
    result = await WorkflowCancellationController(
        HarnessWorkflowRepository(db), McpDocumentationProvider()
    ).cancel(
        owner_user_id=user.id,
        project_scope=await get_user_project_ids(user, db),
        conversation_id=None,
        workflow_reference=str(run_id),
        access_token=request.cookies.get("access_token"),
        expected_version=payload.expected_version,
        reason=payload.reason,
    )
    if result.outcome is WorkflowCancellationOutcome.NO_MATCH:
        raise HTTPException(status_code=404, detail=result.message)
    if result.outcome is WorkflowCancellationOutcome.PROVIDER_UNAVAILABLE:
        raise HTTPException(status_code=503, detail=result.message)
    if result.outcome in {
        WorkflowCancellationOutcome.AMBIGUOUS,
        WorkflowCancellationOutcome.CONFLICT,
        WorkflowCancellationOutcome.NOT_CANCELLABLE,
    }:
        raise HTTPException(status_code=409, detail=result.message)
    if result.run is None:
        raise HTTPException(status_code=409, detail=result.message)
    return WorkflowActionResponse(run=result.run, message=result.message)


@router.post("/workflows/{run_id}/reconcile", response_model=WorkflowActionResponse)
async def reconcile_workflow_run(
    run_id: int,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> WorkflowActionResponse:
    """Poll MCP's authoritative child receipt for one owner-visible workflow.

    The access token is read only for this outbound call. Reconciliation state
    stores bounded status metadata rather than credentials or provider payloads.
    """
    repo = HarnessWorkflowRepository(db)
    scope = await get_user_project_ids(user, db)
    try:
        run = await repo.get_run(scope, run_id, owner_user_id=user.id)
    except HarnessRunNotFoundError:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    if run.capability_id != "mcp.docs.generate_project_documentation":
        raise HTTPException(status_code=409, detail="Workflow does not support documentation reconciliation")
    if not run.children and run.status not in {
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.TRIGGERED,
        WorkflowRunStatus.INDETERMINATE,
    }:
        raise HTTPException(status_code=409, detail="Workflow has no accepted child job to reconcile")
    result = await DocumentationWorkflowReconciler(repo, McpDocumentationProvider()).reconcile(
        run,
        access_token=request.cookies.get("access_token"),
    )
    return WorkflowActionResponse(run=result.run, message=_workflow_action_message(result.run))


def _intersect_scope(scope: list[str] | None, project_id: str | None) -> list[str] | None:
    if project_id is None:
        return scope
    if scope is None:
        return [project_id]
    if project_id in scope:
        return [project_id]
    raise HTTPException(status_code=403, detail="Project access required")


def _parse_status(status: str | None) -> WorkflowRunStatus | None:
    if status is None:
        return None
    try:
        return WorkflowRunStatus(status)
    except ValueError:
        allowed = ", ".join(item.value for item in WorkflowRunStatus)
        raise HTTPException(status_code=400, detail=f"Invalid workflow status. Expected one of: {allowed}")


def _argument_hash(arguments: dict) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _workflow_action_message(run: WorkflowRun) -> str:
    if run.status in {WorkflowRunStatus.PENDING, WorkflowRunStatus.INTERRUPTED}:
        return "Workflow continuation accepted and pending execution"
    if run.status == WorkflowRunStatus.CANCELLED:
        return "Workflow cancelled"
    if run.status == WorkflowRunStatus.FAILED:
        return "Workflow could not continue"
    if run.status == WorkflowRunStatus.INDETERMINATE:
        return "Workflow outcome is being reconciled"
    if run.intent_mode == "generate_docs":
        return "Documentation generation started"
    if run.intent_mode == "project_onboarding":
        return "Project onboarding started"
    if run.intent_mode == "update_docs_from_evidence":
        return "Documentation updated"
    return "Workflow resumed"
