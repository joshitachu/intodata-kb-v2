from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import mcp_server.api.routes.connectors as routes
from mcp_server.api.routes.connectors import ConnectorEvidenceSearchRequest


class _Session:
    def __init__(self) -> None:
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def commit(self) -> None:
        self.committed = True


class _Repository:
    def __init__(self, _session) -> None:
        self.secrets = SimpleNamespace(decrypt_for_provider=lambda _value: "secret-is-stays-here")
        self.audits = []
        self.binding = SimpleNamespace(
            id="binding-1",
            enabled=True,
            capability_ids=["runtime.events.search"],
            source_ids=["src-approved"],
            budgets={"max_calls": 2, "max_items": 4, "max_time_window_days": 5},
            max_time_window_days=5,
        )
        self.instance = SimpleNamespace(
            id="connector-1",
            provider_id="elastic_stack",
            enabled=True,
            status="connected",
            credential_ciphertext="encrypted-value",
        )
        self.sources = [SimpleNamespace(id="src-approved", enabled=True, display_label="logs")]
        self.upserted = []

    async def get_instance(self, _connector_id):
        return self.instance

    async def get_binding(self, _project_id, _connector_id):
        return self.binding

    async def get_sources(self, _connector_id, _source_ids):
        return self.sources

    async def upsert_sources(self, profiles):
        self.upserted.extend(profiles)
        return profiles

    async def audit(self, **kwargs):
        self.audits.append(kwargs)


class _Provider:
    def __init__(self) -> None:
        self.calls = []
        self.discarded = []
        self.described = []

    async def describe(self, instance, source, credential):
        self.described.append((instance, source, credential))
        return SimpleNamespace(
            id=source.id,
            fields={
                "timestamp_field": "@timestamp",
                "search_fields": ["error.message", "message"],
                "message_fields": ["message"],
            },
        )

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        event = SimpleNamespace(
            reference="evref_safe",
            observed_at=dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc),
            fields={"message": "Maximo route failed", "service.name": "maximoFourpsWorkOrder"},
        )
        return SimpleNamespace(items=[event])

    def discard_evidence_references(self, envelope):
        self.discarded.append(envelope)

    async def check_freshness(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "latest_event_at": "2026-09-24T11:30:00Z",
            "events_in_window": 7,
            "total_documents": 100,
            "timestamped_documents": 98,
        }


@pytest.mark.asyncio
async def test_labs_search_requires_project_grant_and_returns_normalized_evidence(monkeypatch):
    session = _Session()
    repository = _Repository(session)
    provider = _Provider()
    monkeypatch.setattr(routes, "async_session_factory", lambda: session)
    monkeypatch.setattr(routes, "ConnectorRepository", lambda _session: repository)
    monkeypatch.setattr(routes, "instance_config", lambda instance: instance)
    monkeypatch.setattr(routes, "source_profile", lambda source, _secrets: source)
    monkeypatch.setattr(routes.connector_catalog, "provider", lambda _provider_id: provider)

    async def source_grant(_session, *, project_id, connector_id, source_ids):
        assert project_id == "project-1"
        assert connector_id == "connector-1"
        return source_ids == ["src-approved"]

    monkeypatch.setattr(routes, "_has_labs_source_grant", source_grant)

    async def request_access(_request, _project_id):
        return None

    monkeypatch.setattr(routes, "require_project_access", request_access)
    request = SimpleNamespace(state=SimpleNamespace(auth_payload={"sub": "7", "username": "owner"}))
    response = await routes.search_bound_elastic_evidence(
        "project-1",
        "connector-1",
        ConnectorEvidenceSearchRequest(
            source_ids=["src-approved"], query="Maximo 4PS", limit=3, time_window_days=20
        ),
        request,
    )

    assert response["items"][0]["title"] == "maximoFourpsWorkOrder"
    assert response["items"][0]["excerpt"] == "Maximo route failed"
    assert response["items"][0]["metadata"]["source"] == "logs"
    assert len(provider.calls) == 1
    assert provider.calls[0]["credential"] == "secret-is-stays-here"
    question = provider.calls[0]["question"]
    assert question.time_to - question.time_from == dt.timedelta(days=7)
    assert question.entity_refs == ("Maximo 4PS",)
    assert len(provider.discarded) == 1
    assert session.committed is True
    assert repository.audits[0]["details"] == {
        "project_id": "project-1",
        "source_count": 1,
        "result_count": 1,
    }


@pytest.mark.asyncio
async def test_labs_search_repairs_legacy_elastic_field_profile_before_query(monkeypatch):
    session = _Session()
    repository = _Repository(session)
    repository.sources[0].field_profile = {
        "timestamp_field": "@timestamp",
        "message_fields": ["LogData.Message.keyword"],
    }
    provider = _Provider()
    monkeypatch.setattr(routes, "async_session_factory", lambda: session)
    monkeypatch.setattr(routes, "ConnectorRepository", lambda _session: repository)
    monkeypatch.setattr(routes, "instance_config", lambda instance: instance)
    monkeypatch.setattr(routes, "source_profile", lambda source, _secrets: source)
    monkeypatch.setattr(routes.connector_catalog, "provider", lambda _provider_id: provider)

    async def source_grant(_session, *, project_id, connector_id, source_ids):
        return (
            project_id == "project-1"
            and connector_id == "connector-1"
            and source_ids == ["src-approved"]
        )

    async def request_access(_request, _project_id):
        return None

    monkeypatch.setattr(routes, "_has_labs_source_grant", source_grant)
    monkeypatch.setattr(routes, "require_project_access", request_access)
    request = SimpleNamespace(state=SimpleNamespace(auth_payload={"sub": "7", "username": "owner"}))

    await routes.search_bound_elastic_evidence(
        "project-1",
        "connector-1",
        ConnectorEvidenceSearchRequest(source_ids=["src-approved"], query="error"),
        request,
    )

    assert len(provider.described) == 1
    assert provider.calls[0]["source"].fields["search_fields"] == ["error.message", "message"]
    assert repository.upserted[0].fields["message_fields"] == ["message"]


@pytest.mark.asyncio
async def test_labs_search_cannot_expand_project_source_grant(monkeypatch):
    session = _Session()
    repository = _Repository(session)
    provider = _Provider()
    monkeypatch.setattr(routes, "async_session_factory", lambda: session)
    monkeypatch.setattr(routes, "ConnectorRepository", lambda _session: repository)
    monkeypatch.setattr(routes.connector_catalog, "provider", lambda _provider_id: provider)

    async def source_grant(_session, *, project_id, connector_id, source_ids):
        assert project_id == "project-1"
        assert connector_id == "connector-1"
        return False

    monkeypatch.setattr(routes, "_has_labs_source_grant", source_grant)

    async def request_access(_request, _project_id):
        return None

    monkeypatch.setattr(routes, "require_project_access", request_access)
    request = SimpleNamespace(state=SimpleNamespace(auth_payload={"sub": "7", "username": "owner"}))
    with pytest.raises(HTTPException) as raised:
        await routes.search_bound_elastic_evidence(
            "project-1",
            "connector-1",
            ConnectorEvidenceSearchRequest(source_ids=["src-other"], query="Maximo"),
            request,
        )

    assert raised.value.status_code == 403
    assert provider.calls == []
    assert session.committed is False


@pytest.mark.asyncio
async def test_labs_freshness_is_project_granted_bounded_and_document_free(monkeypatch):
    from mcp_server.api.routes.connectors import ConnectorEvidenceFreshnessRequest

    session = _Session()
    repository = _Repository(session)
    provider = _Provider()
    monkeypatch.setattr(routes, "async_session_factory", lambda: session)
    monkeypatch.setattr(routes, "ConnectorRepository", lambda _session: repository)
    monkeypatch.setattr(routes, "instance_config", lambda instance: instance)
    monkeypatch.setattr(routes, "source_profile", lambda source, _secrets: source)
    monkeypatch.setattr(routes.connector_catalog, "provider", lambda _provider_id: provider)

    async def source_grant(_session, *, project_id, connector_id, source_ids):
        return (
            project_id == "project-1"
            and connector_id == "connector-1"
            and source_ids == ["src-approved"]
        )

    monkeypatch.setattr(routes, "_has_labs_source_grant", source_grant)

    async def request_access(_request, _project_id):
        return None

    monkeypatch.setattr(routes, "require_project_access", request_access)
    request = SimpleNamespace(state=SimpleNamespace(auth_payload={"sub": "7", "username": "owner"}))
    response = await routes.check_bound_elastic_freshness(
        "project-1",
        "connector-1",
        ConnectorEvidenceFreshnessRequest(source_ids=["src-approved"], time_window_days=20),
        request,
    )

    assert response["window_days"] == 7
    assert response["sources"] == [
        {
            "source_id": "src-approved",
            "source": "logs",
            "status": "available",
            "latest_event_at": "2026-09-24T11:30:00Z",
            "events_in_window": 7,
            "total_documents": 100,
            "timestamped_documents": 98,
        }
    ]
    assert len(provider.calls) == 1
    assert provider.calls[0]["window_days"] == 7
    assert provider.calls[0]["credential"] == "secret-is-stays-here"
    assert session.committed is True
    assert repository.audits[0]["action"] == "connector.evidence.freshness"
