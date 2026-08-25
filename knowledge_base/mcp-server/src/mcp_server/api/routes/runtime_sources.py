"""Dashboard-facing proxy for runtime sources owned by the RAG backend.

Purpose
-------
A Talend Management Console connection is configured from the dashboard's
Connectors page, in one place, like every other connector. Its credential still
has to be stored in the RAG backend's ``runtime_sources`` tables -- a scheduler
there reads it every 60 seconds to mirror executions, and documentation reads
bounded aggregates back out of that mirror. Storing it in the MCP connector
store instead would look connected and never sync. These routes therefore move
the *user experience*, not the storage.

Auth model -- what is checked, and where
----------------------------------------
Two independent checks apply to every call, and a caller must pass BOTH:

1. **Here (MCP).** ``require_connector_view`` for reads and
   ``require_connector_management`` for writes -- the same workspace permissions
   (``view_connectors`` / ``manage_connectors``) that guard connector create and
   delete in ``routes/connectors.py``. This is the dashboard's own authority
   over its Connectors surface.

2. **There (RAG backend), on the real human.** The proxy forwards the caller's
   own ``access_token`` and ``csrf_token`` cookies rather than a machine key, so
   the RAG backend resolves the actual ``User`` and applies
   ``runtime_sources:read`` / ``runtime_sources:write``, the per-project scope
   check ``_assert_scope_allows``, its MFA-enrolment requirement, and its
   ``runtime_source_audit`` entry attributed to that user.

**What RBAC is bypassed: nothing.** This is worth stating because the obvious
implementation does bypass it. ``RagBridge`` talks to the RAG backend with
``MCP_INGEST_API_KEY``, an unscoped machine credential; proxying a user action
over it would erase the per-project ``RUNTIME_SOURCES_WRITE`` check and file the
audit entry under a service account. That path is also simply not available:
these routes authenticate via ``get_current_user``, which reads the
``access_token`` cookie only, so a bearer machine key is answered
``401 Not authenticated`` (verified against the running backend), and unlike
``/api/v1/ingest/*`` they are not CSRF-exempt. Forwarding the caller's cookie
works because both services validate the same JWT -- ``JWT_SECRET_KEY`` is one
shared environment variable -- and is the same technique the RAG app's own
Next.js proxy uses (``RAG_pipeline/frontend/lib/proxyHeaders.ts``).

The composition is strictly narrowing: requiring ``manage_connectors`` here on
top of ``runtime_sources:write`` there means a user granted only the latter must
still use the RAG app directly. That is a deliberate trade -- this is the
dashboard's Connectors page, and its own permission should govern it -- but it
is an AND, so grant both to operators who should manage TMC from here.

Credentials are write-only. No request body is logged in this module, and
neither service returns a stored credential in any response.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from mcp_server.api.permissions import (
    require_connector_management,
    require_connector_view,
)
from mcp_server.schemas import (
    RuntimeDocReadScopeResponse,
    RuntimeSourceCreateRequest,
    RuntimeSourceListResponse,
    RuntimeSourceResponse,
    RuntimeSourceScopeDiscoveryRequest,
    RuntimeSourceScopesResponse,
    RuntimeSourceSyncResponse,
    RuntimeSourceTypeListResponse,
)
from mcp_server.services.runtime_sources_bridge import (
    ForwardedIdentity,
    RuntimeSourcesBridge,
    RuntimeSourcesBridgeError,
    runtime_managed_connector_ids,
)

router = APIRouter(tags=["runtime-sources"])


def _identity(request: Request) -> ForwardedIdentity:
    return ForwardedIdentity.from_cookies(dict(request.cookies))


def _bridge(request: Request) -> RuntimeSourcesBridge:
    """Allow tests (and future callers) to inject a bridge via app state."""
    injected = getattr(request.app.state, "runtime_sources_bridge", None)
    return injected if injected is not None else RuntimeSourcesBridge()


def _raise(exc: RuntimeSourcesBridgeError) -> None:
    """Relay the RAG backend's own status and detail.

    Verbatim on purpose: "That physical runtime source is already registered"
    (409) tells an operator exactly what happened, and the RAG backend has
    already redacted credential-shaped substrings from its error strings.
    """
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/runtime-sources/source-types", response_model=RuntimeSourceTypeListResponse)
async def list_runtime_source_types(request: Request):
    """Credential-form schema for each runtime provider, from the provider itself."""
    await require_connector_view(request)
    try:
        source_types = await _bridge(request).list_source_types(_identity(request))
    except RuntimeSourcesBridgeError as exc:
        _raise(exc)
    return {"source_types": source_types}


@router.get("/runtime-sources", response_model=RuntimeSourceListResponse)
async def list_runtime_sources(
    request: Request,
    project_id: str | None = Query(default=None),
    provider_id: str | None = Query(default=None),
):
    """List runtime sources visible to the caller.

    ``provider_id`` is the connector catalogue id; the bridge narrows to that
    provider's runtime ``source_type`` values (including the historical spelling)
    so a row created before the key was shortened still counts as connected.
    """
    await require_connector_view(request)
    if provider_id is not None and provider_id not in runtime_managed_connector_ids():
        raise HTTPException(
            status_code=400,
            detail=f"{provider_id!r} is not a runtime-managed connector.",
        )
    try:
        sources = await _bridge(request).list_sources(
            _identity(request), project_id=project_id, connector_provider_id=provider_id
        )
    except RuntimeSourcesBridgeError as exc:
        _raise(exc)
    return {"sources": sources, "total": len(sources)}


@router.get("/runtime-sources/read-scope", response_model=RuntimeDocReadScopeResponse)
async def get_runtime_read_scope(request: Request, project_id: str = Query(...)):
    """What a documentation run for this project reads from its runtime mirror.

    ``view_connectors`` rather than ``manage_connectors``: this is a statement
    about what a run will do, which anyone who can see the connector surface
    should be able to read. Changing it stays in the RAG app, where the
    per-project editor lives.
    """
    await require_connector_view(request)
    try:
        scope = await _bridge(request).read_scope(_identity(request), project_id=project_id)
    except RuntimeSourcesBridgeError as exc:
        _raise(exc)
    return scope


@router.post("/runtime-sources/discover", response_model=RuntimeSourceScopesResponse)
async def discover_runtime_source_scopes(
    payload: RuntimeSourceScopeDiscoveryRequest, request: Request
):
    """Test unsaved credentials and list the ingest scopes they can reach.

    Requires ``manage_connectors`` rather than ``view_connectors``: the caller is
    supplying a credential and causing a live call to the vendor API, which is a
    management action even though it writes nothing.
    """
    await require_connector_management(request)
    if payload.provider_id not in runtime_managed_connector_ids():
        raise HTTPException(
            status_code=400,
            detail=f"{payload.provider_id!r} is not a runtime-managed connector.",
        )
    try:
        scopes = await _bridge(request).discover_scopes(
            _identity(request),
            connector_provider_id=payload.provider_id,
            credentials=payload.credentials,
        )
    except RuntimeSourcesBridgeError as exc:
        _raise(exc)
    return {"scopes": scopes}


@router.post("/runtime-sources", response_model=RuntimeSourceResponse, status_code=201)
async def create_runtime_source(payload: RuntimeSourceCreateRequest, request: Request):
    """Register a runtime source, storing the credential in the RAG backend.

    The RAG backend validates the credential against the vendor API and resolves
    the tenant behind it BEFORE persisting anything, so a bad token creates no
    row -- its 400 is a real "Connect & test" result, not an optimistic save.
    """
    await require_connector_management(request)
    if payload.provider_id not in runtime_managed_connector_ids():
        raise HTTPException(
            status_code=400,
            detail=f"{payload.provider_id!r} is not a runtime-managed connector.",
        )
    try:
        source = await _bridge(request).create_source(
            _identity(request),
            connector_provider_id=payload.provider_id,
            project_id=payload.project_id,
            label=payload.label,
            credentials=payload.credentials,
            sync_interval_seconds=payload.sync_interval_seconds,
        )
    except RuntimeSourcesBridgeError as exc:
        _raise(exc)
    return source


@router.post("/runtime-sources/{source_id}/sync", response_model=RuntimeSourceSyncResponse)
async def sync_runtime_source(source_id: int, request: Request):
    """Run one mirror cycle now instead of waiting for the scheduler."""
    await require_connector_management(request)
    try:
        result = await _bridge(request).sync_source(_identity(request), source_id)
    except RuntimeSourcesBridgeError as exc:
        _raise(exc)
    return result


@router.delete("/runtime-sources/{source_id}", status_code=204)
async def delete_runtime_source(source_id: int, request: Request):
    await require_connector_management(request)
    try:
        await _bridge(request).delete_source(_identity(request), source_id)
    except RuntimeSourcesBridgeError as exc:
        _raise(exc)
    return None
