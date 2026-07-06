from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.dependencies import DBSession, RuntimeSourceReader, RuntimeSourceWriter
from src.rbac.permissions import get_user_project_ids
from src.runtime.models import RuntimeWorkspaceProjectMap, RuntimeWorkspaceProjectMapCreate
from src.runtime.store import RuntimeRecordNotFoundError, RuntimeStoreRepository
from src.runtime.audit import log_runtime_source_event

router = APIRouter(prefix="/runtime/sources/{source_id}/workspace-map", tags=["runtime-sources"])


class WorkspaceMapListResponse(BaseModel):
    mappings: list[RuntimeWorkspaceProjectMap]
    total: int


class WorkspaceMapResponse(BaseModel):
    mapping: RuntimeWorkspaceProjectMap


@router.get("", response_model=WorkspaceMapListResponse)
async def list_workspace_map(
    source_id: int,
    user: RuntimeSourceReader,
    db: DBSession,
) -> WorkspaceMapListResponse:
    repo = RuntimeStoreRepository(db)
    try:
        await repo.get_source_config(source_id, await get_user_project_ids(user, db))
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    mappings = await repo.list_mappings(source_id)
    return WorkspaceMapListResponse(mappings=mappings, total=len(mappings))


@router.post("", response_model=WorkspaceMapResponse)
async def add_workspace_map(
    source_id: int,
    body: RuntimeWorkspaceProjectMapCreate,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> WorkspaceMapResponse:
    repo = RuntimeStoreRepository(db)
    try:
        await repo.get_source_config(source_id, await get_user_project_ids(user, db))
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    mapping = await repo.add_container_mapping(
        source_id=source_id,
        container_key=body.container_key,
        container_name=body.container_name,
        project_id=body.project_id,
        created_by=str(user.id),
    )
    await log_runtime_source_event(
        db,
        user=user,
        action="container_mapping_added",
        source_id=source_id,
        details={"container_key": body.container_key, "project_id": body.project_id},
    )
    return WorkspaceMapResponse(mapping=mapping)


@router.delete("/{container_key}", status_code=204)
async def delete_workspace_map(
    source_id: int,
    container_key: str,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> None:
    repo = RuntimeStoreRepository(db)
    try:
        await repo.get_source_config(source_id, await get_user_project_ids(user, db))
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime source not found")
    await repo.delete_mapping(source_id, container_key)
    await log_runtime_source_event(
        db,
        user=user,
        action="container_mapping_removed",
        source_id=source_id,
        details={"container_key": container_key},
    )
