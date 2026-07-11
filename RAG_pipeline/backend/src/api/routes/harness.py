"""API routes for Nexus harness capabilities and workflow receipts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import CurrentUser, DBSession
from src.harness import HarnessRunNotFoundError, HarnessWorkflowRepository, default_capability_registry
from src.harness.manifest import (
    ManifestScope,
    RuntimeCapabilityManifest,
    build_runtime_capability_manifest,
)
from src.harness.models import (
    ActivityEvent,
    CapabilityDefinition,
    RUNTIME_EVIDENCE_CAPABILITY,
    WorkflowRun,
    WorkflowRunStatus,
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


@router.get("/manifest", response_model=RuntimeCapabilityManifest)
async def get_runtime_manifest(
    user: CurrentUser,
    db: DBSession,
    include_unavailable: bool = False,
) -> RuntimeCapabilityManifest:
    """Return the runtime-safe capability manifest for agent orchestration."""
    project_ids = await get_user_project_ids(user, db)
    return build_runtime_capability_manifest(
        default_capability_registry(),
        scope=ManifestScope(
            project_ids=project_ids,
            permissions=get_user_permissions(user),
            include_unavailable=include_unavailable,
        ),
    )


@router.get("/connectors", response_model=HarnessConnectorListResponse)
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
            capability_ids=[RUNTIME_EVIDENCE_CAPABILITY.id],
            risk_tier=RUNTIME_EVIDENCE_CAPABILITY.risk_tier.value,
            enabled=True,
        )
        for provider in all_source_providers()
    ]
    return HarnessConnectorListResponse(connectors=connectors, total=len(connectors))


@router.get("/workflows", response_model=WorkflowRunListResponse)
async def list_workflow_runs(
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
        limit=limit,
    )
    return WorkflowRunListResponse(runs=runs, total=len(runs))


@router.get("/workflows/{run_id}", response_model=WorkflowRunDetailResponse)
async def get_workflow_run(
    run_id: int,
    user: CurrentUser,
    db: DBSession,
) -> WorkflowRunDetailResponse:
    scope = await get_user_project_ids(user, db)
    repo = HarnessWorkflowRepository(db)
    try:
        run = await repo.get_run(scope, run_id)
        activities = await repo.list_activity_events(scope, run_id)
    except HarnessRunNotFoundError:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return WorkflowRunDetailResponse(run=run, activities=activities)


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
