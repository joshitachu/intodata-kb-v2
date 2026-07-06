from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import DBSession, RuntimeSourceReader, RuntimeSourceWriter
from src.rbac.permissions import get_user_project_ids
from src.runtime.models import (
    RuntimeSourceCreate,
    RuntimeSourceRead,
    RuntimeSourceRotateCredentials,
    RuntimeSourceUpdate,
)
from src.runtime.source_registry import get_source_provider
from src.runtime.store import ProjectScope, RuntimeRecordNotFoundError, RuntimeStoreRepository
from src.runtime.sync import run_runtime_source_sync_cycle
from src.runtime.audit import log_runtime_source_event

router = APIRouter(prefix="/runtime/sources", tags=["runtime-sources"])

_SECRET_PATTERN = re.compile(r"Authorization|Bearer|api[_-]?key|token|secret", re.I)


class RuntimeSourceListResponse(BaseModel):
    sources: list[RuntimeSourceRead]
    total: int


class RuntimeSourceResponse(BaseModel):
    source: RuntimeSourceRead


class RuntimeSourceSyncResponse(BaseModel):
    source: RuntimeSourceRead
    status: str
    records_ingested: int


def _intersect_scope(scope: ProjectScope, project_id: str | None) -> ProjectScope:
    if project_id is None:
        return scope
    if scope is None:
        return [project_id]
    if project_id in scope:
        return [project_id]
    return []


def _sanitize_error(value: str | None) -> str | None:
    if value is None:
        return None
    return _SECRET_PATTERN.sub("[redacted]", value)


def _assert_scope_allows(scope: ProjectScope, project_id: str) -> None:
    if scope is not None and project_id not in scope:
        raise HTTPException(status_code=403, detail="Project access required")


@router.get("", response_model=RuntimeSourceListResponse)
async def list_runtime_sources(
    user: RuntimeSourceReader,
    db: DBSession,
    project_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> RuntimeSourceListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    sources = await repo.list_source_configs(_intersect_scope(scope, project_id), project_id=project_id, limit=limit)
    return RuntimeSourceListResponse(sources=sources, total=len(sources))


@router.post("", response_model=RuntimeSourceResponse)
async def create_runtime_source(
    body: RuntimeSourceCreate,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> RuntimeSourceResponse:
    scope = await get_user_project_ids(user, db)
    _assert_scope_allows(scope, body.project_id)
    provider = get_source_provider(body.provider_key)
    if provider is None:
        raise HTTPException(status_code=400, detail="Unknown runtime source type")
    ok, error = await provider.validate_credentials(body.credentials)
    if not ok:
        raise HTTPException(status_code=400, detail=_sanitize_error(error) or "Connection validation failed")
    repo = RuntimeStoreRepository(db)
    source = await repo.create_source_config(
        project_id=body.project_id,
        source_type=provider.provider_key(),
        label=body.label,
        sync_interval_seconds=body.sync_interval_seconds,
    )
    await repo.write_credentials(
        source_id=source.id,
        plaintext_payload=body.credentials,
        credential_type=provider.provider_key(),
        rotated_by=str(user.id),
    )
    await log_runtime_source_event(
        db,
        user=user,
        action="source_created",
        source_id=source.id,
        details={"source_type": source.source_type, "project_id": source.project_id},
    )
    source = await repo.get_source_config(source.id, scope)
    return RuntimeSourceResponse(source=source)


@router.get("/{source_id}", response_model=RuntimeSourceResponse)
async def get_runtime_source(
    source_id: int,
    user: RuntimeSourceReader,
    db: DBSession,
) -> RuntimeSourceResponse:
    repo = RuntimeStoreRepository(db)
    try:
        source = await repo.get_source_config(source_id, await get_user_project_ids(user, db))
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    return RuntimeSourceResponse(source=source)


@router.patch("/{source_id}", response_model=RuntimeSourceResponse)
async def update_runtime_source(
    source_id: int,
    body: RuntimeSourceUpdate,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> RuntimeSourceResponse:
    repo = RuntimeStoreRepository(db)
    try:
        source = await repo.update_source_config(source_id, await get_user_project_ids(user, db), body)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    await log_runtime_source_event(
        db,
        user=user,
        action="source_enabled_manual" if body.enabled is True else "source_disabled_manual" if body.enabled is False else "source_updated",
        source_id=source_id,
        details=body.model_dump(exclude_none=True),
    )
    return RuntimeSourceResponse(source=source)


@router.post("/{source_id}/credentials/rotate", response_model=RuntimeSourceResponse)
async def rotate_runtime_source_credentials(
    source_id: int,
    body: RuntimeSourceRotateCredentials,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> RuntimeSourceResponse:
    repo = RuntimeStoreRepository(db)
    scope = await get_user_project_ids(user, db)
    try:
        source = await repo.get_source_config(source_id, scope)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    provider = get_source_provider(source.source_type)
    if provider is None:
        raise HTTPException(status_code=400, detail="Unknown runtime source type")
    ok, error = await provider.validate_credentials(body.credentials)
    if not ok:
        raise HTTPException(status_code=400, detail=_sanitize_error(error) or "Connection validation failed")
    await repo.write_credentials(
        source_id=source_id,
        plaintext_payload=body.credentials,
        credential_type=source.source_type,
        rotated_by=str(user.id),
    )
    await log_runtime_source_event(
        db,
        user=user,
        action="source_rotated_credentials",
        source_id=source_id,
        details={"source_type": source.source_type},
    )
    return RuntimeSourceResponse(source=await repo.get_source_config(source_id, scope))


@router.post("/{source_id}/sync", response_model=RuntimeSourceSyncResponse)
async def sync_runtime_source_now(
    source_id: int,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> RuntimeSourceSyncResponse:
    repo = RuntimeStoreRepository(db)
    scope = await get_user_project_ids(user, db)
    try:
        await repo.get_source_config(source_id, scope)
        result = await run_runtime_source_sync_cycle(db, source_id=source_id)
        source = await repo.get_source_config(source_id, scope)
    except RuntimeRecordNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    records = (
        result.executions_written
        + result.failures_written
        + result.log_events_written
        + result.component_metrics_written
        + result.sources_written
        + result.environments_written
        + result.workspaces_written
        + result.engines_written
        + result.jobs_written
        + result.artifacts_written
        + result.connections_written
        + result.job_dependencies_written
        + result.job_runs_written
        + result.plans_written
        + result.plan_runs_written
    )
    await log_runtime_source_event(
        db,
        user=user,
        action="source_manual_sync",
        source_id=source_id,
        details={"status": result.status.value, "records_ingested": records},
    )
    return RuntimeSourceSyncResponse(
        source=source,
        status=result.status.value,
        records_ingested=records,
    )


@router.delete("/{source_id}", status_code=204)
async def delete_runtime_source(
    source_id: int,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> None:
    repo = RuntimeStoreRepository(db)
    try:
        await repo.delete_source_config(source_id, await get_user_project_ids(user, db))
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    await log_runtime_source_event(
        db,
        user=user,
        action="source_deleted",
        source_id=source_id,
        details={},
    )
