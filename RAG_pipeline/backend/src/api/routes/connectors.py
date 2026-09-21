"""Owner-managed connector registry exposed to the agent capability runtime.

Connector implementations and policy live in :mod:`src.harness.connectors`.
This module is intentionally only the authenticated HTTP boundary: validate a
wire request, delegate to the service, record an audit event, and commit.
Credential values are never returned; connector views expose only whether an
opaque credential reference exists and which broker scheme it uses.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from src.api.dependencies import DBSession, OwnerUser
from src.api.errors import api_error
from src.harness.connectors import (
    ConnectorNotFoundError,
    ConnectorPolicyError,
    ConnectorRepository,
    ConnectorService,
    default_connector_provider_registry,
)
from src.harness.connectors.models import (
    CapabilityBindingCreate,
    CapabilityBindingView,
    ConnectorHealthResult,
    ConnectorInstanceCreate,
    ConnectorInstanceUpdate,
    ConnectorInstanceView,
    ConnectorManifest,
)
from src.harness.connectors.service import binding_view, instance_view
from src.services.audit_service import AuditService

router = APIRouter(prefix="/connectors", tags=["connectors"])


class ConnectorProviderListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: list[ConnectorManifest]
    total: int


class ConnectorInstanceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connectors: list[ConnectorInstanceView]
    total: int


class ConnectorBindingListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bindings: list[CapabilityBindingView]
    total: int


def _service(db: DBSession) -> ConnectorService:
    return ConnectorService(ConnectorRepository(db), default_connector_provider_registry())


def _translate_error(exc: Exception):  # noqa: ANN202
    if isinstance(exc, ConnectorNotFoundError):
        return api_error(404, "connector_not_found", "Connector or binding not found")
    if isinstance(exc, (ConnectorPolicyError, KeyError, ValueError)):
        return api_error(422, "invalid_connector", str(exc))
    raise exc


async def _audit(
    db: DBSession,
    request: Request,
    *,
    owner: OwnerUser,
    action: str,
    resource_id: int | None = None,
    details: dict | None = None,
) -> None:
    await AuditService(db).log(
        user_id=owner.id,
        username=owner.username,
        action=action,
        resource_type="connector",
        resource_id=str(resource_id) if resource_id is not None else None,
        details=details or {},
        ip_address=request.client.host if request.client else None,
    )


@router.get("/providers", response_model=ConnectorProviderListResponse)
async def list_connector_providers(owner: OwnerUser) -> ConnectorProviderListResponse:
    del owner
    providers = default_connector_provider_registry().manifests()
    return ConnectorProviderListResponse(providers=providers, total=len(providers))


@router.get("", response_model=ConnectorInstanceListResponse)
async def list_connector_instances(
    owner: OwnerUser, db: DBSession
) -> ConnectorInstanceListResponse:
    del owner
    rows = await _service(db).list()
    connectors = [instance_view(row) for row in rows]
    return ConnectorInstanceListResponse(connectors=connectors, total=len(connectors))


@router.post("", response_model=ConnectorInstanceView, status_code=201)
async def create_connector_instance(
    body: ConnectorInstanceCreate,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> ConnectorInstanceView:
    try:
        row = await _service(db).create(body)
        await _audit(
            db,
            request,
            owner=owner,
            action="create_connector",
            resource_id=row.id,
            details={"name": row.name, "provider_key": row.provider_key},
        )
        await db.commit()
        return instance_view(row)
    except Exception as exc:
        await db.rollback()
        raise _translate_error(exc)


@router.patch("/{instance_id}", response_model=ConnectorInstanceView)
async def update_connector_instance(
    instance_id: int,
    body: ConnectorInstanceUpdate,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> ConnectorInstanceView:
    try:
        row = await _service(db).update(instance_id, body)
        await _audit(
            db,
            request,
            owner=owner,
            action="update_connector",
            resource_id=row.id,
            details={"changed_fields": sorted(body.model_fields_set)},
        )
        await db.commit()
        return instance_view(row)
    except Exception as exc:
        await db.rollback()
        raise _translate_error(exc)


@router.delete("/{instance_id}", status_code=204)
async def delete_connector_instance(
    instance_id: int,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> None:
    try:
        await _service(db).delete(instance_id)
        await _audit(
            db,
            request,
            owner=owner,
            action="delete_connector",
            resource_id=instance_id,
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise _translate_error(exc)


@router.post("/{instance_id}/health", response_model=ConnectorHealthResult)
async def check_connector_health(
    instance_id: int,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> ConnectorHealthResult:
    try:
        result = await _service(db).check_health(instance_id)
        await _audit(
            db,
            request,
            owner=owner,
            action="check_connector_health",
            resource_id=instance_id,
            details={"status": result.status.value},
        )
        await db.commit()
        return result
    except Exception as exc:
        await db.rollback()
        raise _translate_error(exc)


@router.get("/{instance_id}/bindings", response_model=ConnectorBindingListResponse)
async def list_connector_bindings(
    instance_id: int, owner: OwnerUser, db: DBSession
) -> ConnectorBindingListResponse:
    del owner
    try:
        rows = await _service(db).list_bindings(instance_id)
    except Exception as exc:
        raise _translate_error(exc)
    bindings = [binding_view(row) for row in rows]
    return ConnectorBindingListResponse(bindings=bindings, total=len(bindings))


@router.post(
    "/{instance_id}/bindings",
    response_model=CapabilityBindingView,
    status_code=201,
)
async def create_connector_binding(
    instance_id: int,
    body: CapabilityBindingCreate,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> CapabilityBindingView:
    # An empty project list means global in the persistence contract. That is a
    # useful internal primitive but too broad for an HTTP default.
    if not body.project_ids:
        raise api_error(
            422,
            "project_scope_required",
            "At least one project_id is required for an agent connector binding",
        )
    try:
        row = await _service(db).bind(instance_id, body)
        await _audit(
            db,
            request,
            owner=owner,
            action="bind_connector",
            resource_id=instance_id,
            details={
                "binding_id": row.id,
                "capability_id": row.capability_id,
                "project_ids": list(row.project_ids),
            },
        )
        await db.commit()
        return binding_view(row)
    except Exception as exc:
        await db.rollback()
        raise _translate_error(exc)


@router.delete("/{instance_id}/bindings/{binding_id}", status_code=204)
async def delete_connector_binding(
    instance_id: int,
    binding_id: int,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> None:
    try:
        await _service(db).unbind(instance_id, binding_id)
        await _audit(
            db,
            request,
            owner=owner,
            action="unbind_connector",
            resource_id=instance_id,
            details={"binding_id": binding_id},
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise _translate_error(exc)
