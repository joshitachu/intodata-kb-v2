from __future__ import annotations

import asyncio
import datetime
import logging
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
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


# One kind remains, and it stays an enumerated field rather than becoming an
# implicit constant: `project_source_type` -- "this project already has a TMC
# source, widen its ingest scope" -- was deleted with the rule behind it, which
# refused a SECOND, DIFFERENT tenant in a project and then pointed the operator
# at a source that mirrors none of it. A closed vocabulary keeps a future
# collision additive for clients instead of a breaking change.
RuntimeSourceConflictKind = Literal["physical_identity"]


class RuntimeSourceConflict(BaseModel):
    """The machine-readable half of a refused registration.

    A registration 409 used to be prose only, which made it a dead end: the
    supported way forward for a tenant that is already registered is to ATTACH
    to the existing mirror, and no client could offer that without first
    knowing WHICH source collided. Naming the source here is what lets the UI
    take the operator to the sharing control instead of restating the refusal.

    ``physical_identity`` is the one collision left, and it is the one that was
    always right: this vendor tenant is mirrored already. Registering it twice
    would poll the vendor twice and leave two mirrors of one estate free to
    drift apart, so attaching IS the answer.

    Every identifying field is OPTIONAL by design. A 409 is reachable by anyone
    holding a working token for the tenant, so unconditional identifying fields
    would turn this error into an existence oracle for sources and projects the
    caller has no access to. See ``_registration_conflict`` for the rule that
    decides what is withheld from whom.
    """

    kind: RuntimeSourceConflictKind
    # Whether THIS caller could attach themselves, not whether an attachment is
    # conceivable. A button that 403s is worse than no button.
    can_request_share: bool
    source_id: int | None = None
    source_label: str | None = None
    # The stamp the colliding source mirrors into -- NOT an owning project, and
    # the field that replaced `owner_project_id`. For a source registered
    # against a project it is that project's id; for one registered against
    # none it is a derived `estate:` key.
    estate_key: str | None = None
    # Every project attached to the colliding source, sorted. `[]` is a real
    # answer -- a tenant registered before any project, which the caller may
    # well be entitled to attach to -- and is therefore NOT the same as the
    # field being absent, which means "withheld".
    linked_project_ids: list[str] | None = None


class RuntimeSourceConflictResponse(BaseModel):
    """409 body for a refused registration.

    ``detail`` stays a plain sentence and stays self-sufficient: the RAG app's
    own admin screen, the dashboard proxy's generic error path and `curl` all
    render that string and nothing else. ``conflict`` is additive for clients
    that can act on it.
    """

    detail: str
    conflict: RuntimeSourceConflict


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


def _assert_registration_scope(scope: ProjectScope, project_id: str | None) -> None:
    """Authorize a registration against the caller's project scope.

    Both branches live in ONE function so the difference between them is a
    decision rather than an omission -- "no project supplied" must not become a
    door around a check that applies when one is.

    A project IS supplied
        Unchanged: the caller must hold it. Supplying a project attaches it to
        the new source, so this is the check that stops an operator wiring a
        tenant into a project they have no access to.

    No project is supplied
        There is no project to scope-check, and adding a manufactured one would
        be a check of nothing. The gate is the route's own
        ``runtime_sources:write`` permission (``RuntimeSourceWriter``), which is
        the same permission the attachment route demands -- and attachment is
        where project access is actually decided, by
        ``add_read_grant``'s two-sided check.

        This cannot be used to bypass the branch above, because the two do
        different things: omitting the project does not register "into" some
        default project, it attaches NOTHING. The source is created with a
        derived estate key, no project reads a single one of its rows, and the
        only way to change that is the attachment route, which re-checks the
        receiving project against the caller's scope. The widest thing a
        project-less registration can do is create a connector -- never grant
        the caller access to anybody's data.

    The one refusal that remains without a project: an EMPTY (non-None) scope.
    That caller holds no project at all, and the store's own visibility rule
    already refuses them every source, including unattached ownerless ones. They
    would create a connector they cannot then see, manage or delete -- an orphan
    poll with no operator -- so the write is refused where the read already is.
    """
    if project_id is not None:
        _assert_scope_allows(scope, project_id)
        return
    if scope is not None and not scope:
        raise HTTPException(
            status_code=403,
            detail="Registering a runtime source requires access to at least one project",
        )


# The way forward, in the caller's own hands...
_SHARE_ACTION_SELF = (
    "Grant this project read access to the existing source instead "
    "(Shared with, on the source), or split the tenant by workspace."
)
# ...and the same way forward when it is not. Deliberately names no project:
# this variant is what a caller who cannot see the colliding source is told.
_SHARE_ACTION_OTHER = (
    "An operator with access to that source has to grant this project read "
    "access to it; registering the tenant a second time is not the way in."
)
# The source exists but no project is attached to it yet -- a tenant registered
# before any project, which is now a supported order of work. Nobody has to be
# asked for anything: the caller is already looking at a source they can reach.
_LINK_ACTION_UNATTACHED = (
    "Link this project to that source instead (Shared with, on the source)."
)
# ...and when the requesting project is ALREADY attached, there is nothing to
# ask for and nothing to link. Offering a share here would be a button that
# re-scopes an attachment the operator did not come here to change.
_ALREADY_LINKED_ACTION = (
    "This project already reads that source; edit it rather than registering "
    "the tenant again."
)


async def _registration_conflict(
    repo: RuntimeStoreRepository,
    *,
    existing_id: int,
    scope: ProjectScope,
    requesting_project_id: str | None,
) -> JSONResponse:
    """Build the 409 for a registration that collided with an existing source.

    The status is 409 and stays 409 -- the refusal is correct. What was wrong
    was that it was a dead end: an operator adding a second project against one
    Talend tenant was told the source exists, with no hint that the same tenant
    is meant to be SHARED rather than registered twice (a second registration
    means a second poll of the vendor and two mirrors of one estate free to
    drift apart). The attachment mechanism already existed; nothing pointed at
    it.

    Why a response rather than an ``HTTPException``: FastAPI's handler renders
    ``{"detail": <detail>}`` and nothing else, so a structured block can only be
    carried by nesting it inside ``detail`` -- which would break every client
    that renders that field as a sentence. Returning the body keeps ``detail``
    a string and puts ``conflict`` beside it.

    Disclosure rule, decided here and stated once
    ---------------------------------------------
    The identifying fields are emitted exactly when ``get_source_config`` would
    return this source to this caller -- which is why that call IS the test,
    rather than a predicate re-derived here that could drift from it. So the
    409 never discloses one byte more than ``GET /runtime/sources/{id}`` already
    hands the same caller, and cannot be an oracle for anything the inventory is
    not already an oracle for.

    Two consequences worth naming, because they changed with the estate key:

    * An UNATTACHED source is disclosed. It belongs to no project, every
      operator who may manage sources can already list it, and the caller may
      well be entitled to attach to it -- withholding it would refuse a
      registration while hiding the one object that explains the refusal.
    * An attached estate the caller cannot reach is withheld exactly as before.
      ``source_id`` is withheld on the same test and not a weaker one: every
      route keyed on it answers 404 for such a caller, so it is a handle to
      nothing, and handing out dense integer ids invites probing.

    What is NEVER withheld: that the tenant is already registered, and that
    attaching is the route forward. The refusal has to remain actionable for the
    operator who must now go and ask someone.
    """
    try:
        # Scope None: read the row as an unrestricted operator, so the fields
        # exist to be withheld deliberately. Reading it ONLY under the caller's
        # scope would make "not visible" indistinguishable from "already
        # deleted", and those get different answers below.
        existing = await repo.get_source_config(existing_id, None)
    except RuntimeRecordNotFoundError:
        # Raced against a delete. The collision was real when it was detected,
        # so still refuse -- but there is nothing left to point at or share.
        return JSONResponse(
            status_code=409,
            content=RuntimeSourceConflictResponse(
                detail=f"That physical runtime source is already registered. {_SHARE_ACTION_SELF}",
                conflict=RuntimeSourceConflict(
                    kind="physical_identity", can_request_share=False
                ),
            ).model_dump(exclude_none=True),
        )
    try:
        await repo.get_source_config(existing_id, scope)
        visible = True
    except RuntimeRecordNotFoundError:
        visible = False

    # Attached, from the attachment table alone: `linked_project_ids` is the
    # complete list, and the estate key is added because a source registered
    # against a project stamps every row with it whether or not a row records
    # the attachment. `add_read_grant` refuses that project for the same reason.
    attached = set(existing.linked_project_ids) | {existing.estate_key}
    already_reads = requesting_project_id is not None and requesting_project_id in attached
    # The conditions `add_read_grant` actually enforces, re-evaluated rather
    # than guessed: the caller must be able to reach the source (`visible`) and
    # hold the receiving project (`_assert_registration_scope` proved that one),
    # and the receiving project must not already read the estate. Reaching this
    # route at all required RUNTIME_SOURCES_WRITE, the same permission the
    # attachment route demands. With no project supplied there is nothing to
    # attach, so no share is on offer however visible the source is.
    can_request_share = visible and requesting_project_id is not None and not already_reads

    if not visible:
        detail = (
            "That physical runtime source is already registered in another project. "
            f"{_SHARE_ACTION_OTHER}"
        )
    else:
        if existing.linked_project_ids:
            projects = ", ".join(f"'{item}'" for item in existing.linked_project_ids)
            where = f"as '{existing.label}' for project {projects}"
        else:
            # Stated positively: "no project yet" is the reason the caller can
            # act on it themselves, not an absence of information.
            where = f"as '{existing.label}', linked to no project yet"
        if already_reads:
            action = _ALREADY_LINKED_ACTION
        elif existing.linked_project_ids:
            action = _SHARE_ACTION_SELF
        else:
            action = _LINK_ACTION_UNATTACHED
        detail = f"That physical runtime source is already registered {where}. {action}"

    conflict = RuntimeSourceConflict(
        kind="physical_identity",
        can_request_share=can_request_share,
        source_id=existing.id if visible else None,
        source_label=existing.label if visible else None,
        estate_key=existing.estate_key if visible else None,
        linked_project_ids=existing.linked_project_ids if visible else None,
    )
    return JSONResponse(
        status_code=409,
        content=RuntimeSourceConflictResponse(detail=detail, conflict=conflict).model_dump(
            exclude_none=True
        ),
    )


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


@router.post(
    "",
    response_model=RuntimeSourceResponse,
    responses={
        409: {
            "model": RuntimeSourceConflictResponse,
            "description": (
                "This vendor tenant is already mirrored. The body names the "
                "colliding source so a client can offer an attachment rather "
                "than repeating the refusal."
            ),
        }
    },
)
async def create_runtime_source(
    body: RuntimeSourceCreate,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> RuntimeSourceResponse | JSONResponse:
    """Register a runtime source, or explain precisely why it cannot be.

    ONE registration rule survives, and it is the one that was always right:
    `physical_identity` is globally unique, so a vendor tenant is mirrored once.
    Registering it twice would poll the vendor twice and leave two mirrors of
    one estate free to drift apart.

    The rule that used to sit beside it -- one source of a type per project --
    is gone. It refused a SECOND, DIFFERENT tenant in a project, which is a
    different credential over a different estate, and advised the operator to
    widen the first source's ingest scope, which reaches nothing at all of the
    second one. That refusal is the bug the product owner reported, and there is
    no narrower version of it worth keeping: the schema constraint behind it
    went with migration 105.

    `body.project_id` is optional. Present, it attaches that project and becomes
    the estate key; absent, the tenant is registered attached to nothing. See
    `_assert_registration_scope` for why the second form is not a weaker gate.

    Every 409 here is returned rather than raised so it can carry a `conflict`
    block beside its sentence -- see `_registration_conflict`.
    """
    scope = await get_user_project_ids(user, db)
    _assert_registration_scope(scope, body.project_id)
    provider = get_source_provider(body.provider_key)
    if provider is None:
        raise HTTPException(status_code=400, detail="Unknown runtime source type")
    repo = RuntimeStoreRepository(db)
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
    existing_id = await repo.find_source_id_by_physical_identity(physical_identity)
    if existing_id is not None:
        return await _registration_conflict(
            repo,
            existing_id=existing_id,
            scope=scope,
            requesting_project_id=body.project_id,
        )
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
            return await _registration_conflict(
                repo,
                existing_id=source_id,
                scope=scope,
                requesting_project_id=body.project_id,
            )
    try:
        source = await repo.create_source_config(
            project_id=body.project_id,
            source_type=provider.provider_key(),
            physical_identity=physical_identity,
            label=body.label,
            sync_interval_seconds=body.sync_interval_seconds,
        )
    except RuntimeDuplicateSourceError as exc:
        # The store's own uniqueness guard, reached when a concurrent
        # registration won the race between the check above and this insert.
        # It is the same dead end as the check it backstops, so it gets the same
        # signpost: re-read which row now holds the tenant rather than parsing
        # the exception's text, which distinguishes nothing. `physical_identity`
        # is the only unique rule left, so a raced insert can trip nothing else
        # -- and if the winner is already gone again, the bare 409 stands.
        raced_id = await repo.find_source_id_by_physical_identity(physical_identity)
        if raced_id is not None:
            return await _registration_conflict(
                repo,
                existing_id=raced_id,
                scope=scope,
                requesting_project_id=body.project_id,
            )
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
        # Both halves of what was registered, because they are now two facts:
        # the stamp its mirrored rows will carry, and the projects (if any)
        # that may read them. An audit line carrying only the estate key could
        # not distinguish a tenant registered for a project from one registered
        # for nobody.
        details={
            "source_type": source.source_type,
            "estate_key": source.estate_key,
            "linked_project_ids": source.linked_project_ids,
        },
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
