"""Owner-managed connector registry exposed to the agent capability runtime.

Connector implementations and policy live in :mod:`src.harness.connectors`.
This module is intentionally only the authenticated HTTP boundary: validate a
wire request, delegate to the service, record an audit event, and commit.
Credential values are never returned; connector views expose only whether an
opaque credential reference exists and which broker scheme it uses.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.api.dependencies import DBSession, OwnerUser
from src.api.errors import api_error
from src.harness.connectors import (
    ConnectorNotFoundError,
    ConnectorPolicyError,
    ConnectorRepository,
    ConnectorService,
    default_connector_provider_registry,
)
from src.harness.connectors.mcp_bridge import (
    ExistingElasticUnavailable,
    McpConnectorBridge,
    RemoteMcpUnavailable,
)
from src.harness.connectors.models import (
    CapabilityBindingCreate,
    CapabilityBindingUpdate,
    CapabilityBindingView,
    ConnectorCapabilityDefinition,
    ConnectorHealthResult,
    ConnectorFreshnessResult,
    ConnectorInstanceCreate,
    ConnectorInstanceUpdate,
    ConnectorInstanceView,
    ConnectorManifest,
    ConnectorQuery,
    ConnectorReadResult,
)
from src.harness.connectors.service import binding_view, instance_view
from src.harness.connectors.remote_mcp import remote_mcp_config
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


class ConnectorCapabilityListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: list[ConnectorCapabilityDefinition]
    total: int


class RemoteMcpCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    server_url: str = Field(min_length=8, max_length=2048)
    authentication: Literal["none", "bearer"] = "none"
    credential: str | None = Field(default=None, min_length=1, max_length=20_000, repr=False)

    @field_validator("credential")
    @classmethod
    def reject_multiline_secret(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value):
            raise ValueError("Credential contains invalid characters")
        return value

    @model_validator(mode="after")
    def validate_authentication(self) -> "RemoteMcpCreateRequest":
        if self.authentication == "bearer" and not self.credential:
            raise ValueError("A bearer token is required")
        if self.authentication == "none" and self.credential is not None:
            raise ValueError("Credential is not allowed without authentication")
        return self


class RemoteMcpCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector: ConnectorInstanceView
    capabilities: list[ConnectorCapabilityDefinition]


class RemoteMcpGrantRequest(BaseModel):
    """One owner decision for the complete Labs-visible MCP tool set."""

    model_config = ConfigDict(extra="forbid")
    capability_ids: list[str] = Field(min_length=1, max_length=100)
    project_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("capability_ids", "project_ids")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Choose each item once")
        return values


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


class ConnectorFreshnessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=255)


class ExistingElasticAccount(BaseModel):
    connector_id: str
    display_name: str
    sources: list[dict[str, str]] = Field(default_factory=list)


class ExistingElasticResponse(BaseModel):
    available: bool
    accounts: list[ExistingElasticAccount] = Field(default_factory=list)


class ExistingElasticUseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=255)
    connector_id: str = Field(min_length=1, max_length=64)
    source_ids: list[str] = Field(min_length=1, max_length=3)

    @field_validator("source_ids")
    @classmethod
    def unique_source_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Choose each Elastic source once")
        return values


class ExistingElasticDiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: str = Field(min_length=1, max_length=64)


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


def _remote_mcp_tools(row: object) -> dict[str, dict]:
    config = getattr(row, "config", None)
    raw_tools = config.get("tools") if isinstance(config, dict) else None
    if not isinstance(raw_tools, list):
        return {}
    tools: dict[str, dict] = {}
    for raw in raw_tools:
        if not isinstance(raw, dict):
            continue
        capability_id = raw.get("capability_id")
        source_id = raw.get("source_id")
        if isinstance(capability_id, str) and isinstance(source_id, str):
            tools[capability_id] = raw
    return tools


async def _apply_remote_mcp_grant(
    bridge: McpConnectorBridge,
    access_token: str,
    *,
    connector_id: str,
    tools: dict[str, dict],
    capability_ids: set[str],
    project_ids: set[str],
    previous_capability_ids: set[str],
    previous_project_ids: set[str],
) -> None:
    """Synchronize an exact reviewed read-only grant to the secret-owning service."""

    source_ids = [str(tools[item]["source_id"]) for item in sorted(capability_ids)]
    for capability_id in sorted(capability_ids):
        tool = tools[capability_id]
        label = str(tool.get("label") or tool.get("name") or capability_id)[:120]
        await bridge.review_remote_mcp_tool(
            access_token,
            connector_id=connector_id,
            source_id=str(tool["source_id"]),
            enabled=True,
            approved_purpose=(
                f"Allow the reviewed read-only tool '{label}' in the explicitly selected "
                "Nexus projects."
            )[:500],
        )
    for project_id in sorted(project_ids):
        await bridge.bind_remote_mcp_project(
            access_token,
            project_id=project_id,
            connector_id=connector_id,
            source_ids=source_ids,
        )
    for project_id in sorted(previous_project_ids - project_ids):
        await bridge.unbind_remote_mcp_project(
            access_token, project_id=project_id, connector_id=connector_id
        )
    for capability_id in sorted(previous_capability_ids - capability_ids):
        tool = tools.get(capability_id)
        if tool is not None:
            await bridge.review_remote_mcp_tool(
                access_token,
                connector_id=connector_id,
                source_id=str(tool["source_id"]),
                enabled=False,
                approved_purpose=None,
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
        connectors = await bridge.connected_elastic_accounts(access_token)
    except ExistingElasticUnavailable:
        return ExistingElasticResponse(available=False)

    async def account_view(connector: dict) -> ExistingElasticAccount:
        connector_id = str(connector.get("id", ""))
        sources: list[dict[str, str]] = []
        try:
            sources = await bridge.existing_elastic_sources(access_token, connector_id)
        except ExistingElasticUnavailable:
            pass
        return ExistingElasticAccount(
            connector_id=connector_id,
            display_name=str(connector.get("display_name") or "Elastic Stack"),
            sources=sources,
        )

    accounts = await asyncio.gather(*(account_view(connector) for connector in connectors))
    return ExistingElasticResponse(available=bool(accounts), accounts=list(accounts))


@router.post("/existing-elastic/discover", response_model=ExistingElasticAccount)
async def discover_existing_elastic_sources(
    body: ExistingElasticDiscoverRequest,
    request: Request,
    owner: OwnerUser,
) -> ExistingElasticAccount:
    """Refresh every visible index, alias, and data stream for one Elastic account."""
    del owner
    access_token = request.cookies.get("access_token")
    if not access_token:
        raise api_error(401, "session_required", "Sign in again to discover Elastic sources")
    bridge = McpConnectorBridge()
    try:
        accounts = await bridge.connected_elastic_accounts(access_token)
        connector = next((item for item in accounts if str(item.get("id", "")) == body.connector_id), None)
        if connector is None:
            raise ExistingElasticUnavailable("The selected Elastic account is unavailable.")
        sources = await bridge.existing_elastic_sources(
            access_token, body.connector_id, refresh=True
        )
        return ExistingElasticAccount(
            connector_id=body.connector_id,
            display_name=str(connector.get("display_name") or "Elastic Stack"),
            sources=sources,
        )
    except ExistingElasticUnavailable as exc:
        raise api_error(422, "existing_elastic_unavailable", str(exc)) from exc


@router.post("/existing-elastic/use", response_model=ConnectorInstanceView)
async def use_existing_elastic_connection(
    body: ExistingElasticUseRequest,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> ConnectorInstanceView:
    """Grant the owner's selected Elastic sources to one project's Labs scope."""
    access_token = request.cookies.get("access_token")
    if not access_token:
        raise api_error(401, "session_required", "Sign in again to use the existing Elastic connection")
    repository = ConnectorRepository(db)
    service = _service(db)
    try:
        selected = await McpConnectorBridge().prepare_existing_elastic(
            access_token,
            connector_id=body.connector_id,
            source_ids=body.source_ids,
        )
        existing = next(
            (row for row in await repository.list_instances()
             if row.provider_key == "mcp_elastic"
             and row.config.get("connector_id") == selected["connector_id"]),
            None,
        )

        project_sources: dict[str, list[str]] = {}
        project_source_contexts: dict[str, list[dict]] = {}
        if existing is not None:
            raw_sources = existing.config.get("project_sources")
            if isinstance(raw_sources, dict):
                project_sources = {
                    str(key): [str(value) for value in values if isinstance(value, str)]
                    for key, values in raw_sources.items() if isinstance(values, list)
                }
            raw_contexts = existing.config.get("project_source_contexts")
            if isinstance(raw_contexts, dict):
                project_source_contexts = {
                    str(key): [value for value in values if isinstance(value, dict)]
                    for key, values in raw_contexts.items() if isinstance(values, list)
                }
            # Convert the original shared-source format the first time an old
            # connector is edited through the project source picker.
            legacy_sources = existing.config.get("source_ids")
            if isinstance(legacy_sources, list) and legacy_sources:
                legacy_contexts = existing.config.get("source_contexts")
                bindings = await service.list_bindings(existing.id)
                for project_id in {
                    project_id for binding in bindings for project_id in binding.project_ids
                }:
                    project_sources.setdefault(
                        project_id,
                        [str(value) for value in legacy_sources if isinstance(value, str)],
                    )
                    if isinstance(legacy_contexts, list):
                        project_source_contexts.setdefault(
                            project_id,
                            [value for value in legacy_contexts if isinstance(value, dict)],
                        )

        project_sources[body.project_id] = selected["source_ids"]
        project_source_contexts[body.project_id] = selected.get("source_contexts", [])
        config = {
            "connector_id": selected["connector_id"],
            "project_sources": project_sources,
            "project_source_contexts": project_source_contexts,
            "max_results": 5,
            "time_window_days": 7,
        }
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


@router.post("/remote-mcp", response_model=RemoteMcpCreateResponse, status_code=201)
async def create_remote_mcp_connector(
    body: RemoteMcpCreateRequest,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> RemoteMcpCreateResponse:
    """Store the secret upstream, snapshot tools, and create a secret-free Labs link."""

    access_token = request.cookies.get("access_token")
    if not access_token:
        raise api_error(401, "session_required", "Sign in again to connect an MCP server")
    bridge = McpConnectorBridge()
    upstream_id: str | None = None
    try:
        upstream, sources = await bridge.create_remote_mcp(
            access_token,
            name=body.name,
            server_url=body.server_url,
            authentication=body.authentication,
            credential=body.credential,
        )
        upstream_id = str(upstream["id"])
        service = _service(db)
        row = await service.create(
            ConnectorInstanceCreate(
                name=body.name,
                provider_key="remote_mcp",
                config=remote_mcp_config(
                    connector_id=upstream_id,
                    server_url=body.server_url,
                    sources=sources,
                ),
                enabled=True,
            )
        )
        await service.repository.set_health(
            row, "healthy", f"Connected remote MCP server with {len(sources)} discovered tool(s)"
        )
        capabilities = list(
            service.providers.capabilities_for_instance(row.provider_key, row.config)
        )
        await _audit(
            db,
            request,
            owner=owner,
            action="create_remote_mcp_connector",
            resource_id=row.id,
            details={"tool_count": len(capabilities), "authentication": body.authentication},
        )
        await db.commit()
        return RemoteMcpCreateResponse(connector=instance_view(row), capabilities=capabilities)
    except RemoteMcpUnavailable as exc:
        await db.rollback()
        raise api_error(422, "remote_mcp_unavailable", str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        if upstream_id:
            try:
                await bridge.delete_remote_mcp(access_token, upstream_id)
            except RemoteMcpUnavailable:
                pass
        raise _translate_error(exc)


@router.put(
    "/remote-mcp/{instance_id}/grants",
    response_model=ConnectorBindingListResponse,
)
async def replace_remote_mcp_grants(
    instance_id: int,
    body: RemoteMcpGrantRequest,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> ConnectorBindingListResponse:
    """Review and project-bind the complete selected MCP tool set in one owner action."""

    access_token = request.cookies.get("access_token")
    if not access_token:
        raise api_error(401, "session_required", "Sign in again to approve MCP tools")
    service = _service(db)
    bridge = McpConnectorBridge()
    previous_capability_ids: set[str] = set()
    previous_project_ids: set[str] = set()
    selected_ids = set(body.capability_ids)
    project_ids = set(body.project_ids)
    upstream_id = ""
    tools: dict[str, dict] = {}
    try:
        connector = await service.repository.get_instance(instance_id)
        if connector.provider_key != "remote_mcp":
            raise ConnectorPolicyError("This endpoint accepts only remote MCP connectors")
        config = connector.config if isinstance(connector.config, dict) else {}
        upstream_id = str(config.get("connector_id") or "")
        if not upstream_id:
            raise ConnectorPolicyError("Remote MCP connector identity is missing")
        tools = _remote_mcp_tools(connector)
        missing = selected_ids - set(tools)
        if missing:
            raise ConnectorPolicyError("Refresh the MCP tool catalogue before approving tools")
        if any(tools[item].get("risk_tier") != "read_only" for item in selected_ids):
            raise ConnectorPolicyError(
                "Only tools explicitly declared read-only can be enabled. MCP actions are coming "
                "after approval controls"
            )

        existing = await service.list_bindings(instance_id)
        previous_capability_ids = {
            binding.capability_id for binding in existing if binding.enabled
        }
        previous_project_ids = {
            project_id
            for binding in existing
            if binding.enabled
            for project_id in binding.project_ids
        }
        by_capability = {binding.capability_id: binding for binding in existing}
        for capability_id in sorted(selected_ids):
            current = by_capability.get(capability_id)
            if current is None:
                await service.bind(
                    instance_id,
                    CapabilityBindingCreate(
                        capability_id=capability_id,
                        project_ids=sorted(project_ids),
                        risk_tier="read_only",
                        enabled=True,
                    ),
                )
            else:
                await service.update_binding(
                    instance_id,
                    current.id,
                    CapabilityBindingUpdate(
                        project_ids=sorted(project_ids),
                        risk_tier="read_only",
                        enabled=True,
                    ),
                )
        for current in existing:
            if current.capability_id not in selected_ids and current.enabled:
                await service.update_binding(
                    instance_id, current.id, CapabilityBindingUpdate(enabled=False)
                )

        await _apply_remote_mcp_grant(
            bridge,
            access_token,
            connector_id=upstream_id,
            tools=tools,
            capability_ids=selected_ids,
            project_ids=project_ids,
            previous_capability_ids=previous_capability_ids,
            previous_project_ids=previous_project_ids,
        )
        await _audit(
            db,
            request,
            owner=owner,
            action="replace_remote_mcp_grants",
            resource_id=instance_id,
            details={
                "capability_count": len(selected_ids),
                "project_ids": sorted(project_ids),
            },
        )
        await db.commit()
        bindings = [binding_view(row) for row in await service.list_bindings(instance_id)]
        return ConnectorBindingListResponse(bindings=bindings, total=len(bindings))
    except Exception as exc:
        await db.rollback()
        if upstream_id and tools:
            try:
                await _apply_remote_mcp_grant(
                    bridge,
                    access_token,
                    connector_id=upstream_id,
                    tools=tools,
                    capability_ids=previous_capability_ids,
                    project_ids=previous_project_ids,
                    previous_capability_ids=selected_ids,
                    previous_project_ids=project_ids,
                )
            except RemoteMcpUnavailable:
                pass
        if isinstance(exc, RemoteMcpUnavailable):
            raise api_error(422, "remote_mcp_unavailable", str(exc)) from exc
        raise _translate_error(exc)


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
        service = _service(db)
        row = await service.repository.get_instance(instance_id)
        upstream_id = (
            row.config.get("connector_id")
            if row.provider_key == "remote_mcp" and isinstance(row.config, dict)
            else None
        )
        if isinstance(upstream_id, str) and upstream_id:
            access_token = request.cookies.get("access_token")
            if not access_token:
                raise ConnectorPolicyError("Deleting an MCP server requires your active session")
            bridge = McpConnectorBridge()
            project_ids = {
                project_id
                for binding in await service.list_bindings(instance_id)
                for project_id in binding.project_ids
            }
            for project_id in sorted(project_ids):
                await bridge.unbind_remote_mcp_project(
                    access_token, project_id=project_id, connector_id=upstream_id
                )
            await bridge.delete_remote_mcp(access_token, upstream_id)
        await service.delete(instance_id)
        await _audit(
            db,
            request,
            owner=owner,
            action="delete_connector",
            resource_id=instance_id,
        )
        await db.commit()
    except RemoteMcpUnavailable as exc:
        await db.rollback()
        raise api_error(422, "remote_mcp_unavailable", str(exc)) from exc
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


@router.post(
    "/{instance_id}/capabilities/discover",
    response_model=ConnectorCapabilityListResponse,
)
async def discover_connector_capabilities(
    instance_id: int,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> ConnectorCapabilityListResponse:
    """Refresh a dynamic connector's bounded tool snapshot without enabling tools."""

    try:
        capabilities = list(
            await _service(db).discover_capabilities(
                instance_id, request.cookies.get("access_token")
            )
        )
        await _audit(
            db,
            request,
            owner=owner,
            action="discover_connector_capabilities",
            resource_id=instance_id,
            details={"capability_count": len(capabilities)},
        )
        await db.commit()
        return ConnectorCapabilityListResponse(
            capabilities=capabilities, total=len(capabilities)
        )
    except RemoteMcpUnavailable as exc:
        await db.rollback()
        raise api_error(422, "remote_mcp_unavailable", str(exc)) from exc
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


@router.post("/{instance_id}/freshness", response_model=ConnectorFreshnessResult)
async def check_connector_freshness(
    instance_id: int,
    body: ConnectorFreshnessRequest,
    request: Request,
    owner: OwnerUser,
    db: DBSession,
) -> ConnectorFreshnessResult:
    """Check the latest event timestamp and recent counts without sampling logs."""
    try:
        result = await _service(db).check_freshness(
            instance_id, body.project_id, access_token=request.cookies.get("access_token")
        )
        await _audit(
            db,
            request,
            owner=owner,
            action="check_connector_freshness",
            resource_id=instance_id,
            details={"project_id": body.project_id, "source_count": len(result.sources)},
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
        service = _service(db)
        connector = await service.repository.get_instance(instance_id)
        if connector.provider_key == "remote_mcp":
            raise ConnectorPolicyError(
                "Use the MCP tool approval screen to update all reviewed project grants together"
            )
        row = await service.bind(instance_id, body)
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
        service = _service(db)
        connector = await service.repository.get_instance(instance_id)
        if connector.provider_key == "remote_mcp":
            raise ConnectorPolicyError(
                "Use the MCP tool approval screen to update all reviewed project grants together"
            )
        await service.unbind(instance_id, binding_id)
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
        service = _service(db)
        connector = await service.repository.get_instance(instance_id)
        if connector.provider_key == "remote_mcp":
            raise ConnectorPolicyError(
                "Use the MCP tool approval screen to update all reviewed project grants together"
            )
        row = await service.update_binding(instance_id, binding_id, body)
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
