"""Owner-managed connector registry exposed to the agent capability runtime.

Connector implementations and policy live in :mod:`src.harness.connectors`.
This module is intentionally only the authenticated HTTP boundary: validate a
wire request, delegate to the service, record an audit event, and commit.
Credential values are never returned; connector views expose only whether an
opaque credential reference exists and which broker scheme it uses.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from src.api.dependencies import DBSession, OwnerUser
from src.api.errors import api_error
from src.harness.connectors import (
    ConnectorNotFoundError,
    ConnectorPolicyError,
    ConnectorRepository,
    ConnectorService,
    default_connector_provider_registry,
)
from src.harness.connectors.mcp_bridge import ExistingElasticUnavailable, McpConnectorBridge
from src.harness.connectors.models import (
    CapabilityBindingCreate,
    CapabilityBindingUpdate,
    CapabilityBindingView,
    ConnectorHealthResult,
    ConnectorInstanceCreate,
    ConnectorInstanceUpdate,
    ConnectorInstanceView,
    ConnectorManifest,
    ConnectorQuery,
    ConnectorReadResult,
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


class ConnectorResourceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resources: list[dict]
    total: int


class ConnectorPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability_id: str = Field(min_length=1, max_length=160)
    project_id: str = Field(min_length=1, max_length=255)
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class ExistingElasticResponse(BaseModel):
    available: bool
    display_name: str | None = None
    sources: list[dict[str, str]] = Field(default_factory=list)


class ExistingElasticUseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=255)


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
    providers = [
        provider for provider in default_connector_provider_registry().manifests()
        if provider.provider_key != "mcp_elastic"
    ]
    return ConnectorProviderListResponse(providers=providers, total=len(providers))


@router.get("/existing-elastic", response_model=ExistingElasticResponse)
async def existing_elastic_connection(
    request: Request, owner: OwnerUser
) -> ExistingElasticResponse:
    """Tell the owner whether the already-managed Nexus Elastic link can be reused."""
    del owner
    access_token = request.cookies.get("access_token")
    if not access_token:
        return ExistingElasticResponse(available=False)
    bridge = McpConnectorBridge()
    try:
        connector = await bridge.existing_elastic(access_token)
    except ExistingElasticUnavailable:
        return ExistingElasticResponse(available=False)
    sources: list[dict[str, str]] = []
    if connector:
        try:
            sources = await bridge.existing_elastic_sources(access_token, str(connector.get("id", "")))
        except ExistingElasticUnavailable:
            # Connection availability and source-label lookup are separate. Keep
            # the enable path usable if an older connection lacks source metadata.
            sources = []
    return ExistingElasticResponse(
        available=connector is not None,
        display_name=str(connector.get("display_name") or "Elastic Stack") if connector else None,
        sources=sources,
    )


@router.post("/existing-elastic/use", response_model=ConnectorInstanceView)
async def use_existing_elastic_connection(
    body: ExistingElasticUseRequest,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> ConnectorInstanceView:
    """Add a small, recommended log-source set to one project's Labs scope."""
    access_token = request.cookies.get("access_token")
    if not access_token:
        raise api_error(401, "session_required", "Sign in again to use the existing Elastic connection")
    repository = ConnectorRepository(db)
    service = _service(db)
    try:
        selected = await McpConnectorBridge().prepare_existing_elastic(access_token)
        config = {
            "connector_id": selected["connector_id"],
            "source_ids": selected["source_ids"],
            "max_results": 5,
            "time_window_days": 7,
        }
        existing = next(
            (row for row in await repository.list_instances()
             if row.provider_key == "mcp_elastic"
             and row.config.get("connector_id") == selected["connector_id"]),
            None,
        )
        if existing is None:
            row = await service.create(ConnectorInstanceCreate(
                name=f"{selected['display_name']} (Nexus)",
                provider_key="mcp_elastic",
                config=config,
                enabled=True,
            ))
        else:
            row = await service.update(existing.id, ConnectorInstanceUpdate(config=config, enabled=True))

        await repository.set_health(row, "healthy", "Using the existing Nexus Elastic connection")
        capability_id = "connector.mcp_elastic.search"
        binding = next(
            (item for item in await service.list_bindings(row.id)
             if item.capability_id == capability_id),
            None,
        )
        if binding is None:
            await service.bind(row.id, CapabilityBindingCreate(
                capability_id=capability_id,
                project_ids=[body.project_id],
                enabled=True,
            ))
        else:
            project_ids = list(dict.fromkeys([*binding.project_ids, body.project_id]))
            await service.update_binding(
                row.id, binding.id, CapabilityBindingUpdate(project_ids=project_ids, enabled=True)
            )
        await _audit(
            db,
            request,
            owner=owner,
            action="use_existing_elastic_connector",
            resource_id=row.id,
            details={"project_id": body.project_id, "source_count": len(selected["source_ids"])},
        )
        await db.commit()
        return instance_view(row)
    except ExistingElasticUnavailable as exc:
        await db.rollback()
        raise api_error(422, "existing_elastic_unavailable", str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        raise _translate_error(exc)


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
        result = await _service(db).check_health(instance_id, request.cookies.get("access_token"))
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


@router.post("/{instance_id}/preview", response_model=ConnectorReadResult)
async def preview_connector_evidence(
    instance_id: int,
    body: ConnectorPreviewRequest,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> ConnectorReadResult:
    """Show an owner exactly what one scoped connector search would return."""
    try:
        result = await _service(db).read(
            instance_id,
            body.capability_id,
            body.project_id,
            None,
            ConnectorQuery(query=body.query, limit=body.limit),
            access_token=request.cookies.get("access_token"),
        )
        await _audit(
            db,
            request,
            owner=owner,
            action="preview_connector_evidence",
            resource_id=instance_id,
            details={
                "capability_id": body.capability_id,
                "project_id": body.project_id,
                "result_count": len(result.items),
            },
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


@router.get("/{instance_id}/resources", response_model=ConnectorResourceListResponse)
async def list_connector_resources(
    instance_id: int, request: Request, owner: OwnerUser, db: DBSession
) -> ConnectorResourceListResponse:
    """Discover bounded provider resources using the registered credential reference."""
    try:
        resources = await _service(db).list_resources(instance_id)
        await _audit(
            db,
            request,
            owner=owner,
            action="discover_connector_resources",
            resource_id=instance_id,
            details={"count": len(resources)},
        )
        await db.commit()
        return ConnectorResourceListResponse(resources=resources, total=len(resources))
    except Exception as exc:
        await db.rollback()
        raise _translate_error(exc)


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


@router.patch("/{instance_id}/bindings/{binding_id}", response_model=CapabilityBindingView)
async def update_connector_binding(
    instance_id: int,
    binding_id: int,
    body: CapabilityBindingUpdate,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> CapabilityBindingView:
    if not body.model_fields_set:
        raise api_error(422, "empty_binding_update", "Choose a field to update")
    if body.project_ids is not None and not body.project_ids:
        raise api_error(422, "project_scope_required", "At least one project_id is required")
    try:
        row = await _service(db).update_binding(instance_id, binding_id, body)
        await _audit(
            db,
            request,
            owner=owner,
            action="update_connector_binding",
            resource_id=instance_id,
            details={"binding_id": binding_id, "changed_fields": sorted(body.model_fields_set)},
        )
        await db.commit()
        return binding_view(row)
    except Exception as exc:
        await db.rollback()
        raise _translate_error(exc)
