from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import DBSession, QdrantClient, RuntimeSourceReader, RuntimeSourceWriter
from src.config import settings
from src.rbac.permissions import get_user_project_ids
from src.runtime.models import (
    RuntimeSourceCreate,
    RuntimeSourceRead,
    RuntimeSourceRotateCredentials,
    RuntimeSourceUpdate,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.base import find_non_ascii_credential_field
from src.runtime.source_registry import get_source_provider
from src.runtime.store import ProjectScope, RuntimeDuplicateSourceError, RuntimeRecordNotFoundError, RuntimeStoreRepository
from src.runtime.sync import run_runtime_source_sync_cycle
from src.runtime.audit import log_runtime_source_event

logger = logging.getLogger(__name__)

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
    bad_field = find_non_ascii_credential_field(provider.credential_schema(), body.credentials)
    if bad_field is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Field '{bad_field}' contains a non-ASCII character. Re-type it instead of pasting from a rich-text source.",
        )
    ok, error = await provider.validate_credentials(body.credentials)
    if not ok:
        raise HTTPException(status_code=400, detail=_sanitize_error(error) or "Connection validation failed")
    repo = RuntimeStoreRepository(db)
    try:
        physical_identity = await provider.resolve_physical_identity(body.credentials)
    except (RuntimeProviderError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=_sanitize_error(str(exc)) or "Could not identify the account behind these credentials",
        )
    endpoint_identity = provider.physical_source_identity(body.credentials)
    source_type_aliases = provider.source_type_aliases()
    if await repo.find_source_id_by_physical_identity(physical_identity) is not None:
        raise HTTPException(status_code=409, detail="That physical runtime source is already registered")
    # Sources registered under an older identity rule cannot be compared against
    # a resolved identity as stored, so re-resolve each from its own credentials
    # and repair the row before judging this registration.
    for source_id in await repo.list_unresolved_identity_source_ids(source_type_aliases, endpoint_identity):
        try:
            credentials, _key_id = await repo.read_credentials(source_id)
            resolved = await provider.resolve_physical_identity(credentials)
            await repo.promote_source_identity(source_id, resolved)
        except RuntimeDuplicateSourceError:
            # Two pre-existing rows resolve to the same tenant: the mirror this
            # guard was built to stop, already in the data. Leave it for an
            # operator to delete instead of blocking an unrelated registration.
            logger.warning("Runtime source %s mirrors an already-identified source", source_id)
            continue
        except (RuntimeProviderError, RuntimeRecordNotFoundError, ValueError) as exc:
            # Stored credentials may be dead (rotated, revoked). That row keeps
            # its stale identity; it cannot collide with a narrowed one anyway.
            logger.warning(
                "Could not re-resolve identity for runtime source %s: %s",
                source_id,
                _sanitize_error(str(exc)),
            )
            continue
        if resolved == physical_identity:
            raise HTTPException(status_code=409, detail="That physical runtime source is already registered")
    try:
        source = await repo.create_source_config(
            project_id=body.project_id,
            source_type=provider.provider_key(),
            physical_identity=physical_identity,
            label=body.label,
            sync_interval_seconds=body.sync_interval_seconds,
        )
    except RuntimeDuplicateSourceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
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
    bad_field = find_non_ascii_credential_field(provider.credential_schema(), body.credentials)
    if bad_field is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Field '{bad_field}' contains a non-ASCII character. Re-type it instead of pasting from a rich-text source.",
        )
    ok, error = await provider.validate_credentials(body.credentials)
    if not ok:
        raise HTTPException(status_code=400, detail=_sanitize_error(error) or "Connection validation failed")
    # A rotated credential can point at a different account than the one this
    # source was registered for. The identity has to follow the credentials,
    # and may not land on an account another source already owns.
    try:
        rotated_identity = await provider.resolve_physical_identity(body.credentials)
        await repo.promote_source_identity(source_id, rotated_identity)
    except (RuntimeProviderError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=_sanitize_error(str(exc)) or "Could not identify the account behind these credentials",
        )
    except RuntimeDuplicateSourceError:
        raise HTTPException(
            status_code=409,
            detail="Those credentials belong to a runtime source that is already registered",
        )
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
    qdrant: QdrantClient,
) -> RuntimeSourceSyncResponse:
    repo = RuntimeStoreRepository(db)
    scope = await get_user_project_ids(user, db)
    try:
        await repo.get_source_config(source_id, scope)
        semantic_indexer = None
        if settings.runtime_semantic_enabled:
            from src.runtime.semantic.service import RuntimeSemanticService

            semantic_indexer = RuntimeSemanticService(qdrant)
        result = await run_runtime_source_sync_cycle(
            db,
            source_id=source_id,
            semantic_indexer=semantic_indexer,
        )
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
