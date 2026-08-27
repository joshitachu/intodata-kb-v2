"""Multi-repo project membership: create, list, add, remove, sync.

The single-repo flow (``POST /mcp/add-repo``) stays exactly as it was. These
routes are the N-repo equivalent:

1. The picker lists repos -- ``GET /mcp/github/app/installations/{id}/repos`` or
   ``GET /mcp/azure-devops/connections/{id}/repos``, both already org-wide.
2. ``POST /mcp/multi-repo/projects`` registers the project and its members, then
   clones every member into its own origin-marked directory.
3. ``GET/POST/DELETE /mcp/multi-repo/projects/{project_id}/members`` edits
   membership later without recreating the project.
4. ``POST /mcp/multi-repo/projects/{project_id}/sync`` fast-forwards members;
   ``repo_identity`` narrows it to one repo, which is the incremental path a
   push event drives.

Scanning and generation are unchanged: the scan sees N origin-marked source
roots, the partition planner turns each into its own scoped run, and each member
gets its own doc set under ``output/{project_id}/{subproject_name}/docs`` beside
the shared cross-repo topology layer.

Ownership: ``installation_id`` and ``azure_devops_connection_id`` arrive from the
client and are authorized against the caller before any clone, exactly as
``add-repo`` does. An unchecked id would let a caller clone through another
tenant's credential.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.schemas import (
    MultiRepoCreateRequest,
    MultiRepoMemberRequest,
    MultiRepoMemberResponse,
    MultiRepoProjectResponse,
    MultiRepoSyncOutcomeResponse,
    StatusResponse,
)
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.repo_sources import (
    PROVIDER_AZURE_DEVOPS,
    PROVIDER_GITHUB,
    SUPPORTED_PROVIDERS,
    MultiRepoSyncService,
    RepoSourceRepository,
    allocate_subproject_name,
    project_repos_root,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["multi-repo"])


def _current_user_id(request: Request) -> int:
    payload = getattr(request.state, "auth_payload", None)
    return int(payload.get("sub", 0)) if payload else 0


async def _authorize_credentials(
    *,
    provider: str,
    installation_id: int | None,
    azure_devops_connection_id: str | None,
    user_id: int,
) -> None:
    """Reject a credential reference the caller does not own.

    Mirrors ``projects.add_repo``: both ids are enumerable, so neither may be
    trusted just because it was supplied.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    if provider == PROVIDER_GITHUB and installation_id:
        from mcp_server.services.github_app_ownership import authorize_installation

        async with async_session_factory() as db:
            if not await authorize_installation(db, int(installation_id), user_id):
                raise HTTPException(
                    status_code=403,
                    detail="You do not have access to this GitHub App installation",
                )
    if provider == PROVIDER_AZURE_DEVOPS:
        if not azure_devops_connection_id:
            raise HTTPException(
                status_code=400,
                detail="azure_devops_connection_id is required for the azure_devops provider",
            )
        from mcp_server.services.azure_devops_connections import authorize_connection

        async with async_session_factory() as db:
            if not await authorize_connection(db, azure_devops_connection_id, user_id):
                raise HTTPException(
                    status_code=403,
                    detail="You do not have access to this Azure DevOps connection",
                )


def _member_response(source) -> MultiRepoMemberResponse:
    return MultiRepoMemberResponse(
        id=source.id,
        provider=source.provider,
        repo_identity=source.repo_identity,
        repo_name=source.repo_name,
        clone_url=source.clone_url,
        branch=source.branch,
        subproject_name=source.subproject_name,
        local_path=source.local_path,
        last_ingested_commit_sha=source.last_ingested_commit_sha,
        last_ingested_at=source.last_ingested_at,
        enabled=source.enabled,
    )


def _sync_response(report) -> list[MultiRepoSyncOutcomeResponse]:
    return [
        MultiRepoSyncOutcomeResponse(
            subproject_name=o.subproject_name,
            repo_identity=o.repo_identity,
            action=o.action,
            head_sha=o.head_sha,
            previous_sha=o.previous_sha,
            changed=o.changed,
            error=o.error,
        )
        for o in report.outcomes
    ]


def _dedupe_members(
    members: list[MultiRepoMemberRequest],
) -> list[MultiRepoMemberRequest]:
    """Drop repeats of the same repo, keeping the first occurrence.

    A picker can submit the same repo twice (selected in two groupings). The
    unique constraint would reject the whole request, so collapse it here where
    the intent is unambiguous.
    """
    seen: set[str] = set()
    unique: list[MultiRepoMemberRequest] = []
    for member in members:
        if member.repo_identity in seen:
            continue
        seen.add(member.repo_identity)
        unique.append(member)
    return unique


@router.post("/multi-repo/projects", response_model=MultiRepoProjectResponse)
async def create_multi_repo_project(req: MultiRepoCreateRequest, request: Request):
    """Register a project spanning several repositories and clone every member."""
    user_id = _current_user_id(request)
    await _authorize_credentials(
        provider=req.provider,
        installation_id=req.installation_id,
        azure_devops_connection_id=req.azure_devops_connection_id,
        user_id=user_id,
    )

    members = _dedupe_members(req.members)
    root = project_repos_root(req.project_id)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        repo = RepoSourceRepository(db)

        await registry.add_project(
            project_id=req.project_id,
            local_path=str(root),
            display_name=req.display_name,
            platform=req.platform,
            # The project's tree is the container of the member clones; no single
            # repo_url can describe it, so it stays NULL and the member rows are
            # the truth.
            repo_url=None,
            source_type="git",
        )

        taken = await repo.taken_subproject_names(req.project_id)
        for member in members:
            name = allocate_subproject_name(member.repo_name, taken)
            taken.add(name)
            await repo.add(
                project_id=req.project_id,
                provider=req.provider,
                repo_identity=member.repo_identity,
                repo_name=member.repo_name,
                clone_url=member.clone_url,
                subproject_name=name,
                branch=member.branch,
                github_installation_id=req.installation_id,
                azure_devops_connection_id=req.azure_devops_connection_id,
            )

        # Assign the creator so the project is visible to them immediately,
        # matching upload-zip.
        if user_id:
            from sqlalchemy import text

            await db.execute(
                text(
                    "INSERT INTO project_assignments "
                    "(user_id, project_id, assigned_at, created_at, updated_at) "
                    "VALUES (:uid, :pid, NOW(), NOW(), NOW()) "
                    "ON CONFLICT (user_id, project_id) DO NOTHING"
                ),
                {"uid": user_id, "pid": req.project_id},
            )
        await db.commit()

    # Clone outside the registration transaction: cloning is slow and a failed
    # member must not roll back the membership rows, which the caller needs in
    # order to retry that one repo.
    async with async_session_factory() as db:
        report = await MultiRepoSyncService(db).sync(req.project_id)
        await db.commit()
        sources = await RepoSourceRepository(db).list_for_project(req.project_id)
        payload = MultiRepoProjectResponse(
            project_id=req.project_id,
            members=[_member_response(s) for s in sources],
            sync=_sync_response(report),
        )

    if report.failed:
        logger.warning(
            "Multi-repo project %s created with %d failed member clone(s): %s",
            req.project_id,
            len(report.failed),
            ", ".join(o.subproject_name for o in report.failed),
        )
    return payload


@router.get(
    "/multi-repo/projects/{project_id}/members",
    response_model=MultiRepoProjectResponse,
)
async def list_members(project_id: str):
    async with async_session_factory() as db:
        sources = await RepoSourceRepository(db).list_for_project(project_id)
    return MultiRepoProjectResponse(
        project_id=project_id, members=[_member_response(s) for s in sources]
    )


@router.post(
    "/multi-repo/projects/{project_id}/members",
    response_model=MultiRepoProjectResponse,
)
async def add_members(project_id: str, req: MultiRepoCreateRequest, request: Request):
    """Add repositories to an existing multi-repo project.

    Members already present are ignored rather than rejected, so re-submitting
    the picker's full selection is safe.
    """
    user_id = _current_user_id(request)
    await _authorize_credentials(
        provider=req.provider,
        installation_id=req.installation_id,
        azure_devops_connection_id=req.azure_devops_connection_id,
        user_id=user_id,
    )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        if await registry.get_project(project_id) is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        repo = RepoSourceRepository(db)
        existing = {s.repo_identity for s in await repo.list_for_project(project_id)}
        taken = await repo.taken_subproject_names(project_id)
        added: list[str] = []
        for member in _dedupe_members(req.members):
            if member.repo_identity in existing:
                continue
            name = allocate_subproject_name(member.repo_name, taken)
            taken.add(name)
            await repo.add(
                project_id=project_id,
                provider=req.provider,
                repo_identity=member.repo_identity,
                repo_name=member.repo_name,
                clone_url=member.clone_url,
                subproject_name=name,
                branch=member.branch,
                github_installation_id=req.installation_id,
                azure_devops_connection_id=req.azure_devops_connection_id,
            )
            added.append(member.repo_identity)
        await db.commit()

    async with async_session_factory() as db:
        # Only the new members are cloned; existing working trees are untouched.
        report = await MultiRepoSyncService(db).sync(project_id, only=set(added))
        await db.commit()
        sources = await RepoSourceRepository(db).list_for_project(project_id)
        return MultiRepoProjectResponse(
            project_id=project_id,
            members=[_member_response(s) for s in sources],
            sync=_sync_response(report),
        )


@router.delete(
    "/multi-repo/projects/{project_id}/members/{source_id}",
    response_model=StatusResponse,
)
async def remove_member(project_id: str, source_id: str):
    """Drop a member from the project.

    The clone directory is left on disk deliberately: removing it would delete
    the only local copy of a working tree that may hold uncommitted state, and
    the next scan ignores it once the row is gone.
    """
    async with async_session_factory() as db:
        repo = RepoSourceRepository(db)
        source = await repo.get(source_id)
        if source is None or source.project_id != project_id:
            raise HTTPException(status_code=404, detail="Member not found for this project")
        await repo.remove(source_id)
        await db.commit()
    return StatusResponse(status="ok", message=f"Removed {source.subproject_name}")


@router.post(
    "/multi-repo/projects/{project_id}/sync",
    response_model=MultiRepoProjectResponse,
)
async def sync_members(project_id: str, repo_identity: str | None = None):
    """Fast-forward member clones.

    ``repo_identity`` restricts the sync to one repository -- the incremental
    path. Omitting it syncs every member, which is the full-refresh path.
    """
    async with async_session_factory() as db:
        if await ProjectRegistry(db).get_project(project_id) is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        report = await MultiRepoSyncService(db).sync(
            project_id, only={repo_identity} if repo_identity else None
        )
        await db.commit()
        sources = await RepoSourceRepository(db).list_for_project(project_id)
        return MultiRepoProjectResponse(
            project_id=project_id,
            members=[_member_response(s) for s in sources],
            sync=_sync_response(report),
        )
