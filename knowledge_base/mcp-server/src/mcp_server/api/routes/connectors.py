"""Management and project-scoped REST surface for governed connectors."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from mcp_server.api.permissions import (
    require_connector_management,
    require_connector_view,
    require_project_access,
    require_project_management,
)
from mcp_server.connectors.catalog import connector_catalog
from mcp_server.connectors.errors import ConnectorError
from mcp_server.connectors.repository import ConnectorRepository
from mcp_server.connectors.service import ConnectorService
from mcp_server.db.session import async_session_factory
from mcp_server.schemas import (
    ConnectorCatalogResponse,
    ConnectorCreateRequest,
    ConnectorDiscoverRequest,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorRotateCredentialRequest,
    ConnectorSourceListResponse,
    ConnectorSourceResponse,
    ConnectorTestResponse,
    ConnectorUpdateRequest,
    KibanaDataViewListResponse,
    KibanaSpaceListResponse,
    ProjectAvailableConnector,
    ProjectAvailableConnectorsResponse,
    ProjectConnectorBindingListResponse,
    ProjectConnectorBindingRequest,
    ProjectConnectorBindingResponse,
)


router = APIRouter(tags=["connectors"])


def _actor(request: Request) -> tuple[int, str | None]:
    payload = request.state.auth_payload
    return int(payload["sub"]), payload.get("username")


def _raise_connector_error(exc: ConnectorError) -> None:
    status = 404 if exc.code in {"connector_not_found", "project_not_found", "not_found"} else 409 if exc.code == "active_bindings" else 400
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message}) from exc


def _connector_response(row: object) -> ConnectorResponse:
    return ConnectorResponse(
        id=row.id, provider_id=row.provider_id, display_name=row.display_name, ownership_scope=row.ownership_scope,
        enabled=row.enabled, status=row.status, last_checked_at=row.last_checked_at, health_code=row.health_code,
        provider_version=row.provider_version, redaction_policy_version=row.redaction_policy_version,
        has_credential=bool(row.credential_ciphertext), has_kibana_credential=bool(row.kibana_credential_ciphertext),
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _source_response(row: object) -> ConnectorSourceResponse:
    return ConnectorSourceResponse(
        id=row.id, connector_instance_id=row.connector_instance_id, source_type=row.source_type,
        display_label=row.display_label, field_profile=dict(row.field_profile or {}), discovered_at=row.discovered_at,
        discovery_digest=row.discovery_digest, enabled=row.enabled,
    )


def _binding_response(row: object) -> ProjectConnectorBindingResponse:
    return ProjectConnectorBindingResponse(
        id=row.id, project_id=row.project_id, connector_instance_id=row.connector_instance_id,
        source_ids=list(row.source_ids or []), capability_ids=list(row.capability_ids or []),
        budgets=dict(row.budgets or {}), max_time_window_days=row.max_time_window_days,
        enabled=row.enabled, version=row.version, created_at=row.created_at, updated_at=row.updated_at,
    )


@router.get("/connectors/catalog", response_model=ConnectorCatalogResponse)
async def connector_catalogue(request: Request):
    await require_connector_view(request)
    return {"providers": [
        {
            "provider_id": manifest.provider_id, "label": manifest.label, "version": manifest.version,
            "authentication_types": list(manifest.authentication_types),
            "redaction_policy_version": manifest.redaction_policy_version,
            "kind": manifest.kind.value,
            "build_state": manifest.build_state.value,
            "description": manifest.description,
            # Field NAMES and shapes only. A stored secret is never returned:
            # `secret_fields` tells a client which inputs to render write-only,
            # it does not carry their values.
            "configuration_schema": dict(manifest.configuration_schema),
            "secret_fields": list(manifest.secret_fields),
            "capabilities": [
                {"id": cap.id, "label": cap.label, "description": cap.description, "read_only": cap.read_only,
                 "gather_eligible": cap.gather_eligible, "risk": cap.risk.value,
                 "accepted_entity_ref_types": list(cap.accepted_entity_ref_types), "use_when": cap.use_when,
                 "do_not_use_when": cap.do_not_use_when}
                for cap in manifest.capabilities
            ],
        } for manifest in connector_catalog.manifests()
    ]}


@router.get("/connectors", response_model=ConnectorListResponse)
async def list_connectors(request: Request):
    await require_connector_view(request)
    async with async_session_factory() as session:
        return {"connectors": [_connector_response(row) for row in await ConnectorRepository(session).list_instances()]}


@router.post("/connectors", response_model=ConnectorResponse, status_code=201)
async def create_connector(payload: ConnectorCreateRequest, request: Request):
    await require_connector_management(request)
    user_id, username = _actor(request)
    try:
        async with async_session_factory() as session:
            row = await ConnectorService(session).create_instance(
                provider_id=payload.provider_id, display_name=payload.display_name, safe_config=payload.safe_config,
                credential_type=payload.credential_type, credential=payload.credential,
                kibana_credential=payload.kibana_credential, user_id=user_id, username=username,
            )
            await session.commit()
            return _connector_response(row)
    except (ConnectorError, ValueError) as exc:
        if isinstance(exc, ConnectorError):
            _raise_connector_error(exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/connectors/{connector_id}", response_model=ConnectorResponse)
async def get_connector(connector_id: str, request: Request):
    await require_connector_view(request)
    async with async_session_factory() as session:
        row = await ConnectorRepository(session).get_instance(connector_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Connector was not found")
        return _connector_response(row)


@router.patch("/connectors/{connector_id}", response_model=ConnectorResponse)
async def update_connector(connector_id: str, payload: ConnectorUpdateRequest, request: Request):
    await require_connector_management(request)
    user_id, username = _actor(request)
    try:
        async with async_session_factory() as session:
            row = await ConnectorService(session).update_instance(
                connector_id, display_name=payload.display_name, safe_config=payload.safe_config,
                enabled=payload.enabled, user_id=user_id, username=username,
            )
            await session.commit()
            return _connector_response(row)
    except ConnectorError as exc:
        _raise_connector_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/connectors/{connector_id}", status_code=204)
async def delete_connector(connector_id: str, request: Request):
    await require_connector_management(request)
    user_id, username = _actor(request)
    try:
        async with async_session_factory() as session:
            await ConnectorService(session).delete_instance(connector_id, user_id=user_id, username=username)
            await session.commit()
    except ConnectorError as exc:
        _raise_connector_error(exc)


@router.post("/connectors/{connector_id}/rotate-credential", response_model=ConnectorResponse)
async def rotate_connector_credential(connector_id: str, payload: ConnectorRotateCredentialRequest, request: Request):
    await require_connector_management(request)
    user_id, username = _actor(request)
    try:
        async with async_session_factory() as session:
            await ConnectorService(session).rotate_credential(
                connector_id, credential=payload.credential, credential_type=payload.credential_type,
                kibana_credential=payload.kibana_credential,
                reuse_elasticsearch_credential=payload.reuse_elasticsearch_credential,
                user_id=user_id, username=username,
            )
            await session.commit()
            row = await ConnectorRepository(session).get_instance(connector_id)
            assert row is not None
            return _connector_response(row)
    except ConnectorError as exc:
        _raise_connector_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/connectors/{connector_id}/test", response_model=ConnectorTestResponse)
async def test_connector(connector_id: str, request: Request):
    await require_connector_management(request)
    user_id, username = _actor(request)
    try:
        async with async_session_factory() as session:
            result = await ConnectorService(session).test_instance(connector_id, user_id=user_id, username=username)
            await session.commit()
            return result
    except ConnectorError as exc:
        _raise_connector_error(exc)


@router.get("/connectors/{connector_id}/sources", response_model=ConnectorSourceListResponse)
async def list_connector_sources(connector_id: str, request: Request):
    await require_connector_view(request)
    async with async_session_factory() as session:
        repository = ConnectorRepository(session)
        if await repository.get_instance(connector_id) is None:
            raise HTTPException(status_code=404, detail="Connector was not found")
        rows = await repository.list_sources(connector_id)
        return {"connector_instance_id": connector_id, "sources": [_source_response(row) for row in rows], "total": len(rows)}


@router.post("/connectors/{connector_id}/discover", response_model=ConnectorSourceListResponse)
async def discover_connector(connector_id: str, request: Request, payload: ConnectorDiscoverRequest = ConnectorDiscoverRequest()):
    await require_connector_management(request)
    user_id, username = _actor(request)
    try:
        async with async_session_factory() as session:
            rows = await ConnectorService(session).discover(
                connector_id, selected_source_ids=payload.source_ids or None, include_kibana=payload.include_kibana,
                kibana_space=payload.kibana_space, data_view_id=payload.data_view_id, user_id=user_id, username=username,
            )
            await session.commit()
            return {"connector_instance_id": connector_id, "sources": [_source_response(row) for row in rows], "total": len(rows)}
    except ConnectorError as exc:
        _raise_connector_error(exc)


@router.get("/connectors/{connector_id}/kibana/spaces", response_model=KibanaSpaceListResponse)
async def list_kibana_spaces(connector_id: str, request: Request):
    await require_connector_management(request)
    try:
        async with async_session_factory() as session:
            return {"spaces": await ConnectorService(session).kibana_spaces(connector_id)}
    except ConnectorError as exc:
        _raise_connector_error(exc)


@router.get("/connectors/{connector_id}/kibana/spaces/{space}/data-views", response_model=KibanaDataViewListResponse)
async def list_kibana_data_views(connector_id: str, space: str, request: Request):
    await require_connector_management(request)
    try:
        async with async_session_factory() as session:
            return {"data_views": await ConnectorService(session).kibana_data_views(connector_id, space)}
    except ConnectorError as exc:
        _raise_connector_error(exc)


@router.get("/projects/{project_id}/connector-bindings", response_model=ProjectConnectorBindingListResponse)
async def list_project_connector_bindings(project_id: str, request: Request):
    await require_project_access(request, project_id)
    async with async_session_factory() as session:
        return {"project_id": project_id, "bindings": [_binding_response(row) for row in await ConnectorRepository(session).list_bindings(project_id)]}


@router.put("/projects/{project_id}/connector-bindings/{connector_id}", response_model=ProjectConnectorBindingResponse)
async def upsert_project_connector_binding(project_id: str, connector_id: str, payload: ProjectConnectorBindingRequest, request: Request):
    await require_project_management(request, project_id)
    user_id, username = _actor(request)
    try:
        async with async_session_factory() as session:
            row = await ConnectorService(session).upsert_binding(
                project_id=project_id, connector_id=connector_id, source_ids=payload.source_ids,
                capability_ids=payload.capability_ids, budgets=payload.budgets,
                max_time_window_days=payload.max_time_window_days, enabled=payload.enabled,
                user_id=user_id, username=username,
            )
            await session.commit()
            return _binding_response(row)
    except ConnectorError as exc:
        _raise_connector_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/projects/{project_id}/connector-bindings/{connector_id}", status_code=204)
async def delete_project_connector_binding(project_id: str, connector_id: str, request: Request):
    await require_project_management(request, project_id)
    user_id, username = _actor(request)
    async with async_session_factory() as session:
        repository = ConnectorRepository(session)
        deleted = await repository.delete_binding(project_id, connector_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Connector binding was not found")
        await repository.audit(user_id=user_id, username=username, action="connector.binding.delete",
                               resource_type="project_connector_binding", resource_id=f"{project_id}:{connector_id}", details={})
        await session.commit()


@router.get("/projects/{project_id}/available-connectors", response_model=ProjectAvailableConnectorsResponse)
async def project_available_connectors(project_id: str, request: Request):
    """Safe source metadata for owners configuring a binding; no credentials/locators."""
    await require_project_management(request, project_id)
    async with async_session_factory() as session:
        repository = ConnectorRepository(session)
        available: list[ProjectAvailableConnector] = []
        for connector in await repository.list_instances():
            available.append(ProjectAvailableConnector(
                connector=_connector_response(connector),
                sources=[_source_response(source) for source in await repository.list_sources(connector.id)],
            ))
        return {"connectors": available}
