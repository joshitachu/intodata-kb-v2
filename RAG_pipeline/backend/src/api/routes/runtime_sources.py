from __future__ import annotations

import asyncio
import datetime
import logging
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import DBSession, QdrantClient, RuntimeSourceReader, RuntimeSourceWriter
from src.config import settings
from src.rbac.permissions import get_user_project_ids
from src.runtime.models import (
    RuntimeScopeOption,
    RuntimeSourceConfigUpdate,
    RuntimeSourceCreate,
    RuntimeSourceRead,
    RuntimeSourceRotateCredentials,
    RuntimeSourceScopeDiscovery,
    RuntimeSourceUpdate,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.base import editable_config_fields, find_non_ascii_credential_field
from src.runtime.source_registry import get_source_provider
from src.runtime.store import ProjectScope, RuntimeDuplicateSourceError, RuntimeRecordNotFoundError, RuntimeStoreRepository
from src.runtime.sync import run_runtime_source_sync_cycle
from src.runtime.sync.progress import sync_progress
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


class RuntimeSourceScopesResponse(BaseModel):
    scopes: list[RuntimeScopeOption]


class RuntimeSourceSyncStatusResponse(BaseModel):
    """Live state of an in-flight sync; all fields absent when nothing runs."""

    running: bool
    phase: str | None = None
    records: int = 0
    started_at: datetime.datetime | None = None


def _assert_scope_selection_valid(provider, credentials: dict) -> None:
    """Reject a save that leaves a required ingest scope unset.

    Without this an operator who skips the connection test silently registers a
    whole-tenant poll, which is the expensive default the selection exists to
    avoid.
    """
    selection = provider.scope_selection()
    if selection is None or not selection.required:
        return
    value = credentials.get(selection.field)
    chosen = [item for item in value if str(item).strip()] if isinstance(value, list) else []
    if not chosen:
        raise HTTPException(
            status_code=400,
            detail=f"Select at least one {selection.label.rstrip('s').lower()} before saving",
        )


async def _discover_scopes(provider, credentials: dict) -> list[RuntimeScopeOption]:
    """Run a provider's connection test and return what it can reach."""
    bad_field = find_non_ascii_credential_field(provider.credential_schema(), credentials)
    if bad_field is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Field '{bad_field}' contains a non-ASCII character. Re-type it instead of pasting from a rich-text source.",
        )
    ok, error = await provider.validate_credentials(credentials)
    if not ok:
        raise HTTPException(status_code=400, detail=_sanitize_error(error) or "Connection validation failed")
    try:
        return await provider.discover_scopes(credentials)
    except (RuntimeProviderError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=_sanitize_error(str(exc)) or "Could not list the scopes for these credentials",
        )


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


@router.post("/discover", response_model=RuntimeSourceScopesResponse)
async def discover_runtime_source_scopes(
    body: RuntimeSourceScopeDiscovery,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> RuntimeSourceScopesResponse:
    """Test unsaved credentials and list the ingest scopes they can reach.

    This is the Add-source dialog's "test connection": a 200 means the
    credentials work, and the payload is what the operator picks from. Nothing
    is written, so it is safe to call repeatedly while typing.
    """
    provider = get_source_provider(body.provider_key)
    if provider is None:
        raise HTTPException(status_code=400, detail="Unknown runtime source type")
    return RuntimeSourceScopesResponse(scopes=await _discover_scopes(provider, body.credentials))


@router.post("/{source_id}/scopes", response_model=RuntimeSourceScopesResponse)
async def list_runtime_source_scopes(
    source_id: int,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> RuntimeSourceScopesResponse:
    """List the ingest scopes reachable with a registered source's stored credentials."""
    repo = RuntimeStoreRepository(db)
    try:
        source = await repo.get_source_config(source_id, await get_user_project_ids(user, db))
        credentials, _key_id = await repo.read_credentials(source_id)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    provider = get_source_provider(source.source_type)
    if provider is None:
        raise HTTPException(status_code=400, detail="Unknown runtime source type")
    return RuntimeSourceScopesResponse(scopes=await _discover_scopes(provider, credentials))


@router.patch("/{source_id}/config", response_model=RuntimeSourceResponse)
async def update_runtime_source_config(
    source_id: int,
    body: RuntimeSourceConfigUpdate,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> RuntimeSourceResponse:
    """Update the non-secret settings stored beside a source's credentials.

    Ingest scope, filters, and history windows live in the credential blob
    because that is this connector framework's per-source config store. Editing
    them through the rotation path would force an operator to re-type the API
    token, so this route merges the declared non-secret fields in place and
    leaves the secret untouched. Identity-bearing fields stay frozen: changing
    one would repoint the source at a different account while keeping the
    history ingested from the old one.
    """
    repo = RuntimeStoreRepository(db)
    scope = await get_user_project_ids(user, db)
    try:
        source = await repo.get_source_config(source_id, scope)
        credentials, _key_id = await repo.read_credentials(source_id)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    provider = get_source_provider(source.source_type)
    if provider is None:
        raise HTTPException(status_code=400, detail="Unknown runtime source type")
    allowed = set(editable_config_fields(provider))
    rejected = sorted(set(body.config) - allowed)
    if rejected:
        raise HTTPException(
            status_code=400,
            detail=f"These fields cannot be changed here: {', '.join(rejected)}",
        )
    merged = {**credentials, **body.config}
    _assert_scope_selection_valid(provider, merged)
    ok, error = await provider.validate_credentials(merged)
    if not ok:
        raise HTTPException(status_code=400, detail=_sanitize_error(error) or "Configuration is not valid")
    await repo.write_credentials(
        source_id=source_id,
        plaintext_payload=merged,
        credential_type=source.source_type,
        rotated_by=str(user.id),
    )
    await log_runtime_source_event(
        db,
        user=user,
        action="source_config_updated",
        source_id=source_id,
        # Values can name environments and filters but never a secret: the
        # allow-list above excludes every field the provider marks secret.
        details={"fields": sorted(body.config)},
    )
    return RuntimeSourceResponse(source=await repo.get_source_config(source_id, scope))


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
    repo = RuntimeStoreRepository(db)
    if provider.provider_key() == "tmc" and await repo.find_source_id_by_project_and_type(
        body.project_id, "tmc"
    ) is not None:
        raise HTTPException(
            status_code=409,
            detail="This project already has a TMC source. Edit its ingest scope to add environments.",
        )
    bad_field = find_non_ascii_credential_field(provider.credential_schema(), body.credentials)
    if bad_field is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Field '{bad_field}' contains a non-ASCII character. Re-type it instead of pasting from a rich-text source.",
        )
    _assert_scope_selection_valid(provider, body.credentials)
    ok, error = await provider.validate_credentials(body.credentials)
    if not ok:
        raise HTTPException(status_code=400, detail=_sanitize_error(error) or "Connection validation failed")
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
    # A rotation replaces the secret, not the source's configuration. Carrying
    # the stored non-secret settings forward keeps the ingest scope, filters,
    # and history window intact instead of silently resetting them to defaults
    # whenever a token is renewed.
    try:
        stored, _key_id = await repo.read_credentials(source_id)
    except RuntimeRecordNotFoundError:
        stored = {}
    rotated_credentials = {**stored, **body.credentials}
    _assert_scope_selection_valid(provider, rotated_credentials)
    ok, error = await provider.validate_credentials(rotated_credentials)
    if not ok:
        raise HTTPException(status_code=400, detail=_sanitize_error(error) or "Connection validation failed")
    # A rotated credential can point at a different account than the one this
    # source was registered for. The identity has to follow the credentials,
    # and may not land on an account another source already owns.
    try:
        rotated_identity = await provider.resolve_physical_identity(rotated_credentials)
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
        plaintext_payload=rotated_credentials,
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


async def _run_sync_detached(source_id: int, semantic_indexer) -> None:
    """Run one cycle on its own session, outside the request that started it."""
    from src.db.session import async_session_factory

    async with async_session_factory() as session:
        try:
            await run_runtime_source_sync_cycle(
                session, source_id=source_id, semantic_indexer=semantic_indexer
            )
            await session.commit()
        except Exception:
            await session.rollback()
            # The cycle records its own failure on the source's health row; this
            # only catches what escapes that, and must not kill the task silently.
            logger.exception("Detached runtime sync failed for source %s", source_id)
        finally:
            sync_progress.finish(source_id)


@router.get("/{source_id}/sync", response_model=RuntimeSourceSyncStatusResponse)
async def get_runtime_source_sync_status(
    source_id: int,
    user: RuntimeSourceReader,
    db: DBSession,
) -> RuntimeSourceSyncStatusResponse:
    """Report whether a sync is in flight, and what it is doing."""
    repo = RuntimeStoreRepository(db)
    try:
        await repo.get_source_config(source_id, await get_user_project_ids(user, db))
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    progress = sync_progress.get(source_id)
    if progress is None:
        return RuntimeSourceSyncStatusResponse(running=False)
    return RuntimeSourceSyncStatusResponse(
        running=True,
        phase=progress.phase,
        records=progress.records,
        started_at=progress.started_at,
    )


@router.post("/{source_id}/sync", response_model=RuntimeSourceSyncResponse)
async def sync_runtime_source_now(
    source_id: int,
    user: RuntimeSourceWriter,
    db: DBSession,
    qdrant: QdrantClient,
) -> RuntimeSourceSyncResponse:
    """Start one sync cycle and return immediately.

    A full cycle takes minutes -- every executable's run history, plus a log
    download per failure -- so awaiting it here left the caller with a hanging
    request, no progress, and usually a client timeout that read as "nothing
    happened". Worse, there was no guard: each retry of that apparently-dead
    button launched ANOTHER complete import of the same tenant alongside the
    first. The cycle now runs detached and the caller polls GET on this path.
    """
    repo = RuntimeStoreRepository(db)
    scope = await get_user_project_ids(user, db)
    try:
        source = await repo.get_source_config(source_id, scope)
    except RuntimeRecordNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if sync_progress.get(source_id) is not None:
        raise HTTPException(status_code=409, detail="A sync is already running for this source")
    semantic_indexer = None
    if settings.runtime_semantic_enabled:
        from src.runtime.semantic.service import RuntimeSemanticService

        semantic_indexer = RuntimeSemanticService(qdrant)
    # Claimed here rather than inside the cycle so a second POST arriving before
    # the task is scheduled is still refused.
    sync_progress.begin(source_id, now=datetime.datetime.now(datetime.UTC))
    asyncio.create_task(_run_sync_detached(source_id, semantic_indexer))
    await log_runtime_source_event(
        db,
        user=user,
        action="source_manual_sync",
        source_id=source_id,
        details={"status": "started"},
    )
    return RuntimeSourceSyncResponse(source=source, status="started", records_ingested=0)


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
