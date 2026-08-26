"""Read grants over a runtime source's mirror.

One vendor tenant commonly backs several scanned projects. Registration
refuses the same credential twice -- a second row would mean a second poll and
two mirrors of one estate free to drift apart -- so a second project reaches
that tenant by being granted READ over the existing mirror, not by registering
it again.

Sibling of `runtime_workspace_map`, and deliberately not merged with it. That
one SPLITS a source's rows between projects at write time (each project owns
different containers); this one SHARES the same rows with a second reader.
Presenting them as one control is how an operator picks the wrong one.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.dependencies import DBSession, RuntimeSourceReader, RuntimeSourceWriter
from src.rbac.permissions import get_user_project_ids
from src.runtime.audit import log_runtime_source_event
from src.runtime.models import RuntimeSourceReadGrant, RuntimeSourceReadGrantCreate
from src.runtime.store import (
    RuntimeDuplicateSourceError,
    RuntimeRecordNotFoundError,
    RuntimeStoreRepository,
)

router = APIRouter(prefix="/runtime/sources/{source_id}/readers", tags=["runtime-sources"])


class ReadGrantListResponse(BaseModel):
    grants: list[RuntimeSourceReadGrant]
    total: int


class ReadGrantResponse(BaseModel):
    grant: RuntimeSourceReadGrant


@router.get("", response_model=ReadGrantListResponse)
async def list_read_grants(
    source_id: int,
    user: RuntimeSourceReader,
    db: DBSession,
) -> ReadGrantListResponse:
    repo = RuntimeStoreRepository(db)
    try:
        await repo.get_source_config(source_id, await get_user_project_ids(user, db))
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    grants = await repo.list_read_grants(source_id)
    return ReadGrantListResponse(grants=grants, total=len(grants))


@router.post("", response_model=ReadGrantResponse)
async def add_read_grant(
    source_id: int,
    body: RuntimeSourceReadGrantCreate,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> ReadGrantResponse:
    """Grant one project read access to this source's mirror.

    Authorization requires BOTH sides. A grant widens what a documentation run
    sees, and `/runtime/doc-evidence` -- which consumes it -- authenticates with
    a machine key and has no user scope to check it against. This route is
    therefore the only gate, so it must not be passable by someone who can see
    only one end of the share.
    """
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    try:
        # Enforces the granting side: `get_source_config` applies the caller's
        # scope, so a source in a project they cannot see is a 404.
        await repo.get_source_config(source_id, scope)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    # ...and the receiving side. `scope is None` is an unrestricted operator.
    if scope is not None and body.project_id not in scope:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        grant = await repo.add_read_grant(
            source_id=source_id,
            project_id=body.project_id,
            environment_ids=body.environment_ids,
            window_days=body.window_days,
            created_by=str(user.id),
        )
    except RuntimeDuplicateSourceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await log_runtime_source_event(
        db,
        user=user,
        action="read_granted",
        source_id=source_id,
        details={
            "project_id": grant.project_id,
            # Recorded because a grant filters by project id, so it also exposes
            # any project this source's workspace map re-points rows to. The
            # audit trail has to show the real blast radius, not the intent.
            "exposes_project_ids": grant.exposes_project_ids,
            "environment_ids": grant.environment_ids,
            "window_days": grant.window_days,
        },
    )
    return ReadGrantResponse(grant=grant)


@router.delete("/{project_id}", status_code=204)
async def delete_read_grant(
    source_id: int,
    project_id: str,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> None:
    """Revoke a grant.

    Either side may revoke: the source owner, or an operator on the reading
    project giving up an access they hold. Dropping your own read is not a
    privilege escalation, and requiring the owner for it would strand a reader
    who no longer wants a shared tenant in their documents.
    """
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    if scope is None or project_id in scope:
        holds_reader_side = True
    else:
        holds_reader_side = False
    try:
        await repo.get_source_config(source_id, scope)
    except RuntimeRecordNotFoundError:
        if not holds_reader_side:
            raise HTTPException(status_code=404, detail="Runtime source not found")
    await repo.delete_read_grant(source_id, project_id)
    await log_runtime_source_event(
        db,
        user=user,
        action="read_revoked",
        source_id=source_id,
        details={"project_id": project_id},
    )
