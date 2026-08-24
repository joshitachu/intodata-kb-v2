"""Azure DevOps organization connection lifecycle.

The connect flow the UI drives:

1. ``POST /azure-devops/connections`` with an organization URL + PAT. The token is
   verified against Azure DevOps before it is stored, so a saved connection always
   represents a credential that worked.
2. ``GET  /azure-devops/connections`` — what the user has connected (never tokens).
3. ``GET  /azure-devops/connections/{id}/repos`` — the repo picker's list.
4. ``POST /mcp/add-repo`` with ``azure_devops_connection_id`` + the chosen clone URL
   registers the project and clones it; scanning proceeds exactly as for GitHub.

Every endpoint that names a connection id authorizes it against the ownership
binding first: the id comes from the client and must never be trusted.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from mcp_server.db.session import async_session_factory
from mcp_server.schemas import (
    AzureDevOpsConnectionItem,
    AzureDevOpsConnectionsResponse,
    AzureDevOpsConnectRequest,
    AzureDevOpsRepoItem,
    AzureDevOpsReposResponse,
    StatusResponse,
)
from mcp_server.services.azure_devops_connections import (
    authorize_connection,
    create_or_update_connection,
    delete_connection,
    get_connection,
    list_connections,
    resolve_pat,
    touch_verified,
)
from mcp_server.utils.azure_devops_api import (
    AzureDevOpsError,
    AzureDevOpsUrlError,
    list_repositories,
    verify_connection,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/azure-devops", tags=["azure-devops"])


def _current_user_id(request: Request) -> int:
    auth_payload = getattr(request.state, "auth_payload", None)
    user_id = int(auth_payload.get("sub", 0)) if auth_payload else 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


def _http_error(exc: AzureDevOpsError) -> HTTPException:
    """Map an Azure DevOps failure to the right status.

    A rejected credential or a wrong URL is the caller's to fix (4xx); an
    unreachable or failing Azure DevOps is not (502), so clients can retry rather
    than telling the user their token is bad.
    """
    if exc.retryable:
        return HTTPException(status_code=502, detail=str(exc))
    if exc.status in {401, 403, 404}:
        return HTTPException(status_code=400 if exc.status == 404 else exc.status, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def _to_item(connection) -> AzureDevOpsConnectionItem:
    return AzureDevOpsConnectionItem(
        id=connection.id,
        organization_url=connection.organization_url,
        organization_name=connection.organization_name,
        account_name=connection.account_name,
        last_verified_at=connection.last_verified_at,
    )


async def _authorized_connection(connection_id: str, user_id: int):
    """Return the connection when *user_id* may use it, else raise 403/404."""
    async with async_session_factory() as db:
        if not await authorize_connection(db, connection_id, user_id):
            # 403 whether the row is missing or owned by someone else: a 404 here
            # would confirm which connection ids exist.
            raise HTTPException(
                status_code=403, detail="You do not have access to this Azure DevOps connection"
            )
        connection = await get_connection(db, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Azure DevOps connection not found")
    return connection


@router.post("/connections", response_model=AzureDevOpsConnectionItem)
async def connect_organization(req: AzureDevOpsConnectRequest, request: Request):
    """Verify a PAT against its organization and store it encrypted."""
    user_id = _current_user_id(request)
    try:
        async with async_session_factory() as db:
            connection = await create_or_update_connection(
                db,
                owner_user_id=user_id,
                organization_url=req.organization_url,
                pat=req.pat,
            )
            await db.commit()
            return _to_item(connection)
    except AzureDevOpsUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AzureDevOpsError as exc:
        raise _http_error(exc)


@router.get("/connections", response_model=AzureDevOpsConnectionsResponse)
async def list_organization_connections(request: Request):
    """List the caller's Azure DevOps connections. Tokens are never returned."""
    user_id = _current_user_id(request)
    async with async_session_factory() as db:
        connections = await list_connections(db, user_id)
    return AzureDevOpsConnectionsResponse(
        connections=[_to_item(connection) for connection in connections]
    )


@router.post("/connections/{connection_id}/test", response_model=AzureDevOpsConnectionItem)
async def test_organization_connection(connection_id: str, request: Request):
    """Re-verify a stored PAT against Azure DevOps (tokens expire silently)."""
    user_id = _current_user_id(request)
    connection = await _authorized_connection(connection_id, user_id)
    async with async_session_factory() as db:
        pat = await resolve_pat(db, connection_id)
    if not pat:
        raise HTTPException(
            status_code=400,
            detail="The stored credential could not be read. Reconnect the organization.",
        )
    try:
        account_name = await verify_connection(connection.organization_url, pat)
    except AzureDevOpsError as exc:
        raise _http_error(exc)
    async with async_session_factory() as db:
        await touch_verified(db, connection_id)
        await db.commit()
        refreshed = await get_connection(db, connection_id)
    item = _to_item(refreshed or connection)
    return item.model_copy(update={"account_name": account_name or item.account_name})


@router.get("/connections/{connection_id}/repos", response_model=AzureDevOpsReposResponse)
async def list_connection_repos(connection_id: str, request: Request):
    """List every repository the connection's PAT can see, for the repo picker."""
    user_id = _current_user_id(request)
    connection = await _authorized_connection(connection_id, user_id)
    async with async_session_factory() as db:
        pat = await resolve_pat(db, connection_id)
    if not pat:
        raise HTTPException(
            status_code=400,
            detail="The stored credential could not be read. Reconnect the organization.",
        )
    try:
        repos = await list_repositories(connection.organization_url, pat)
    except AzureDevOpsError as exc:
        logger.warning(
            "Azure DevOps repository listing failed for connection %s: %s", connection_id, exc
        )
        raise _http_error(exc)
    return AzureDevOpsReposResponse(
        connection_id=connection_id,
        organization_name=connection.organization_name,
        repositories=[AzureDevOpsRepoItem(**repo) for repo in repos],
    )


@router.delete("/connections/{connection_id}", response_model=StatusResponse)
async def disconnect_organization(connection_id: str, request: Request):
    """Delete a connection and unbind the projects that used it."""
    user_id = _current_user_id(request)
    await _authorized_connection(connection_id, user_id)
    async with async_session_factory() as db:
        deleted = await delete_connection(db, connection_id)
        await db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Azure DevOps connection not found")
    return StatusResponse(
        status="deleted", message=f"Azure DevOps connection {connection_id} removed"
    )
