"""Management and project-scoped REST surface for governed connectors."""

from __future__ import annotations

import datetime as dt
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from mcp_server.api.permissions import (
    require_connector_management,
    require_connector_view,
    require_project_access,
    require_project_management,
)
from mcp_server.connectors.catalog import connector_catalog
from mcp_server.connectors.domain import EvidenceQuestion
from mcp_server.connectors.errors import ConnectorError
from mcp_server.connectors.repository import (
    ConnectorRepository,
    instance_config,
    source_profile,
)
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


class ConnectorEvidenceSearchRequest(BaseModel):
    """Small, bounded search contract for Labs; callers never send Elastic DSL."""

    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] = Field(min_length=1, max_length=3)
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)
    time_window_days: int = Field(default=7, ge=1, le=30)

    @field_validator("source_ids")
    @classmethod
    def unique_source_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("A source may only be searched once per request")
        return value


class ConnectorEvidenceFreshnessRequest(BaseModel):
    """Bounded, document-free freshness check for granted Elastic sources."""

    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] = Field(min_length=1, max_length=3)
    time_window_days: int = Field(default=7, ge=1, le=30)

    @field_validator("source_ids")
    @classmethod
    def unique_source_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("A source may only be checked once per request")
        return value


class ElasticSourceFreshnessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source: str
    status: Literal["available", "unavailable"]
    latest_event_at: str | None = None
    events_in_window: int | None = None
    total_documents: int | None = None
    timestamped_documents: int | None = None
    error_code: str | None = None
    error: str | None = None


class ConnectorEvidenceFreshnessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked_at: str
    window_days: int
    sources: list[ElasticSourceFreshnessResponse]


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
            "managed_by": manifest.managed_by.value,
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


async def _has_labs_source_grant(session, *, project_id: str, connector_id: str, source_ids: list[str]) -> bool:
    """Check the Labs-owned project binding without changing 8001 connector grants."""
    result = await session.execute(text("""
        SELECT i.config, i.enabled AS instance_enabled, i.health_status,
               b.project_ids, b.enabled AS binding_enabled, b.capability_id
        FROM harness_connector_instances i
        JOIN harness_connector_capability_bindings b ON b.connector_instance_id = i.id
        WHERE i.provider_key = 'mcp_elastic'
    """))
    requested = set(source_ids)
    for row in result.mappings():
        config = row["config"]
        project_ids = row["project_ids"]
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except ValueError:
                continue
        if isinstance(project_ids, str):
            try:
                project_ids = json.loads(project_ids)
            except ValueError:
                continue
        if not isinstance(config, dict) or not isinstance(project_ids, list):
            continue
        project_sources = config.get("project_sources")
        allowed_sources = (
            project_sources.get(project_id, [])
            if isinstance(project_sources, dict) else config.get("source_ids", [])
        )
        if (
            row["instance_enabled"] is True
            and row["health_status"] == "healthy"
            and row["binding_enabled"] is True
            and row["capability_id"] == "connector.mcp_elastic.search"
            and project_id in project_ids
            and config.get("connector_id") == connector_id
            and isinstance(allowed_sources, list)
            and requested.issubset(set(allowed_sources))
        ):
            return True
    return False


@router.post("/projects/{project_id}/connectors/{connector_id}/search")
async def search_bound_elastic_evidence(
    project_id: str,
    connector_id: str,
    payload: ConnectorEvidenceSearchRequest,
    request: Request,
):
    """Search only Elastic sources explicitly granted to this project.

    This is the Labs bridge to the already-managed connector. The caller's
    session cookie is forwarded by RAG; no credential leaves this service and
    neither raw Elastic DSL nor index locators are accepted from the caller.
    """
    await require_project_access(request, project_id)
    actor_id, username = _actor(request)
    async with async_session_factory() as session:
        repository = ConnectorRepository(session)
        instance = await repository.get_instance(connector_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="Connector was not found")
        if instance.provider_id != "elastic_stack" or not instance.enabled or instance.status != "connected":
            raise HTTPException(status_code=409, detail="The existing Elastic connection is not ready")

        if not await _has_labs_source_grant(
            session,
            project_id=project_id,
            connector_id=connector_id,
            source_ids=payload.source_ids,
        ):
            raise HTTPException(status_code=403, detail="A requested log source is outside this project's access")
        max_calls = 3
        if len(set(payload.source_ids)) > max_calls:
            raise HTTPException(status_code=403, detail="The project connector call budget is too small for this search")
        sources = await repository.get_sources(connector_id, payload.source_ids)
        if len(sources) != len(set(payload.source_ids)) or any(not source.enabled for source in sources):
            raise HTTPException(status_code=404, detail="An approved log source is unavailable")

        window_days = min(payload.time_window_days, 7)
        limit = min(payload.limit, 10)
        provider = connector_catalog.provider(instance.provider_id)
        credential = repository.secrets.decrypt_for_provider(instance.credential_ciphertext)
        now = dt.datetime.now(dt.timezone.utc)
        question = EvidenceQuestion(
            question=payload.query,
            entity_refs=(payload.query,),
            time_from=now - dt.timedelta(days=window_days),
            time_to=now,
            limit=limit,
        )
        items: list[dict[str, object]] = []
        try:
            for source in sources:
                remaining = limit - len(items)
                if remaining <= 0:
                    break
                envelope = await provider.execute(
                    instance=instance_config(instance),
                    source=source_profile(source, repository.secrets),
                    credential=credential,
                    capability_id="runtime.events.search",
                    question=EvidenceQuestion(
                        question=payload.query,
                        entity_refs=question.entity_refs,
                        time_from=question.time_from,
                        time_to=question.time_to,
                        limit=remaining,
                    ),
                    run_id=actor_id,
                    project_id=project_id,
                )
                label = source.display_label[:120]
                for evidence in envelope.items[:remaining]:
                    fields = dict(evidence.fields)
                    message = next((
                        value for key, value in fields.items()
                        if any(term in key.casefold() for term in ("message", "error", "log"))
                    ), None)
                    excerpt = message if isinstance(message, str) else json.dumps(fields, ensure_ascii=False, default=str)
                    title = next((
                        str(value) for key, value in fields.items()
                        if any(term in key.casefold() for term in ("service", "job", "name")) and value
                    ), label)
                    items.append({
                        "title": title[:200],
                        "excerpt": excerpt[:2000],
                        "source_ref": evidence.reference[:255],
                        "metadata": {
                            "source": label,
                            "observed_at": evidence.observed_at.isoformat() if evidence.observed_at else None,
                            "fields": fields,
                        },
                    })
                discard = getattr(provider, "discard_evidence_references", None)
                if callable(discard):
                    discard(envelope)
        except ConnectorError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message}) from exc

        await repository.audit(
            user_id=actor_id,
            username=username,
            action="connector.evidence.search",
            resource_type="project_connector_binding",
            resource_id=f"{project_id}:{connector_id}",
            details={"project_id": project_id, "source_count": len(sources), "result_count": len(items)},
        )
        await session.commit()
        return {
            "items": items,
            "truncated": len(items) >= limit,
            "diagnostics": {
                "outcome": "matches_found" if items else "freshness_check_required",
                "query": payload.query,
                "window_days": window_days,
                "searched_sources": [source.display_label[:120] for source in sources],
            },
        }


@router.post(
    "/projects/{project_id}/connectors/{connector_id}/freshness",
    response_model=ConnectorEvidenceFreshnessResponse,
)
async def check_bound_elastic_freshness(
    project_id: str,
    connector_id: str,
    payload: ConnectorEvidenceFreshnessRequest,
    request: Request,
) -> dict[str, object]:
    """Check latest timestamps and recent document counts, without reading events."""
    await require_project_access(request, project_id)
    actor_id, username = _actor(request)
    async with async_session_factory() as session:
        repository = ConnectorRepository(session)
        instance = await repository.get_instance(connector_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="Connector was not found")
        if instance.provider_id != "elastic_stack" or not instance.enabled or instance.status != "connected":
            raise HTTPException(status_code=409, detail="The existing Elastic connection is not ready")
        if not await _has_labs_source_grant(
            session,
            project_id=project_id,
            connector_id=connector_id,
            source_ids=payload.source_ids,
        ):
            raise HTTPException(status_code=403, detail="A requested log source is outside this project's access")
        sources = await repository.get_sources(connector_id, payload.source_ids)
        if len(sources) != len(set(payload.source_ids)) or any(not source.enabled for source in sources):
            raise HTTPException(status_code=404, detail="An approved log source is unavailable")

        # Labs itself is deliberately limited to seven days. Keep this diagnostic
        # aligned with the real question path while also reporting the newest
        # timestamp from the source's full indexed history.
        window_days = min(payload.time_window_days, 7)
        checked_at = dt.datetime.now(dt.timezone.utc)
        provider = connector_catalog.provider(instance.provider_id)
        credential = repository.secrets.decrypt_for_provider(instance.credential_ciphertext)
        results: list[dict[str, object]] = []
        for source in sources:
            try:
                measured = await provider.check_freshness(
                    instance=instance_config(instance),
                    source=source_profile(source, repository.secrets),
                    credential=credential,
                    checked_at=checked_at,
                    window_days=window_days,
                )
                results.append({
                    "source_id": source.id,
                    "source": source.display_label[:120],
                    "status": "available",
                    **measured,
                })
            except ConnectorError as exc:
                results.append({
                    "source_id": source.id,
                    "source": source.display_label[:120],
                    "status": "unavailable",
                    "error_code": exc.code,
                    "error": exc.message,
                })

        await repository.audit(
            user_id=actor_id,
            username=username,
            action="connector.evidence.freshness",
            resource_type="project_connector_binding",
            resource_id=f"{project_id}:{connector_id}",
            details={"project_id": project_id, "source_count": len(sources)},
        )
        await session.commit()
        return {"checked_at": checked_at.isoformat(), "window_days": window_days, "sources": results}
