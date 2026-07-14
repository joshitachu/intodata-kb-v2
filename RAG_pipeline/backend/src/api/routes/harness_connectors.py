"""Safe CRUD and health API for generic harness connectors.

The router exposes configured connector instances while provider manifests
remain the extension boundary for future connector implementations.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from src.api.dependencies import AdminUser, CurrentUser, DBSession
from src.rbac.permissions import get_user_project_ids
from src.harness.connectors.fixture import default_connector_provider_registry
from src.harness.connectors.models import (
    CapabilityBindingCreate, CapabilityBindingView, ConnectorHealthResult,
    ConnectorInstanceCreate, ConnectorInstanceUpdate, ConnectorInstanceView,
    ConnectorManifest,
)
from src.harness.connectors.repository import ConnectorNotFoundError, ConnectorRepository
from src.harness.connectors.service import (
    ConnectorPolicyError, ConnectorService, binding_view, instance_view,
)

router = APIRouter(prefix="/harness/connectors", tags=["harness-connectors"])


class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorInstanceView]
    total: int


def _service(db: DBSession) -> ConnectorService:
    return ConnectorService(ConnectorRepository(db), default_connector_provider_registry())


@router.get("/providers", response_model=list[ConnectorManifest])
async def list_connector_providers(user: CurrentUser) -> list[ConnectorManifest]:
    _ = user
    return default_connector_provider_registry().manifests()


@router.get("", response_model=ConnectorListResponse)
async def list_connectors(user: CurrentUser, db: DBSession) -> ConnectorListResponse:
    rows = await ConnectorRepository(db).list_instances_for_scope(
        await get_user_project_ids(user, db)
    )
    return ConnectorListResponse(connectors=[instance_view(row) for row in rows], total=len(rows))


@router.post("", response_model=ConnectorInstanceView, status_code=status.HTTP_201_CREATED)
async def create_connector(body: ConnectorInstanceCreate, admin: AdminUser, db: DBSession):
    _ = admin
    try:
        return instance_view(await _service(db).create(body))
    except (KeyError, ValueError, ConnectorPolicyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{instance_id}", response_model=ConnectorInstanceView)
async def update_connector(instance_id: int, body: ConnectorInstanceUpdate, admin: AdminUser, db: DBSession):
    _ = admin
    try:
        return instance_view(await _service(db).update(instance_id, body))
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Connector not found") from exc
    except (KeyError, ValueError, ConnectorPolicyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(instance_id: int, admin: AdminUser, db: DBSession) -> Response:
    _ = admin
    repo = ConnectorRepository(db)
    try:
        await repo.delete_instance(await repo.get_instance(instance_id))
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Connector not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{instance_id}/bindings", response_model=CapabilityBindingView, status_code=status.HTTP_201_CREATED)
async def create_binding(instance_id: int, body: CapabilityBindingCreate, admin: AdminUser, db: DBSession):
    _ = admin
    try:
        return binding_view(await _service(db).bind(instance_id, body))
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Connector not found") from exc
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{instance_id}/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_binding(instance_id: int, binding_id: int, admin: AdminUser, db: DBSession) -> Response:
    _ = admin
    try:
        await ConnectorRepository(db).delete_binding(instance_id, binding_id)
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Connector binding not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{instance_id}/health", response_model=ConnectorHealthResult)
async def check_connector_health(instance_id: int, admin: AdminUser, db: DBSession):
    _ = admin
    try:
        return await _service(db).check_health(instance_id)
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Connector not found") from exc
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
