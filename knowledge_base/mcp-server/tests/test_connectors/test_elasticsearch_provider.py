import datetime as dt

import pytest

from mcp_server.connectors.domain import ConnectorInstanceConfig, ConnectorSourceProfile, EvidenceQuestion
from mcp_server.connectors.providers.elasticsearch import ElasticsearchProvider


class FakeTransport:
    def __init__(self):
        self.calls = []

    async def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/_resolve/index/*"):
            return {"indices": [{"name": "logs-prod"}], "aliases": [], "data_streams": []}, 200
        if url.endswith("/_search"):
            if kwargs["json"]["size"] == 0:
                return {"aggregations": {"severity": {"buckets": [{"key": "error", "doc_count": 2}]}}}, 200
            return {"hits": {"hits": [{"_id": "raw-id", "_index": "logs-prod", "_source": {
                "@timestamp": "2026-08-20T00:00:00Z", "message": "timeout", "service.name": "worker", "password": "ignored"
            }}]}}, 200
        raise AssertionError(url)


def _instance():
    return ConnectorInstanceConfig("con", "elastic_stack", "Elastic", {"endpoint": "https://elastic.example", "auth_type": "api_key"})


def _source():
    return ConnectorSourceProfile(
        "src", "con", "index", "Production logs", "logs-prod",
        {"timestamp_field": "@timestamp", "message_fields": ["message"], "service_field": "service.name",
         "severity_field": "severity", "max_time_window_days": 30, "samples_allowed": True},
    )


@pytest.mark.asyncio
async def test_discovery_returns_non_hidden_profiles_only():
    provider = ElasticsearchProvider(FakeTransport())
    sources = await provider.discover(_instance(), "credential")
    assert [source.locator for source in sources] == ["logs-prod"]


@pytest.mark.asyncio
async def test_search_builds_server_side_bounded_query_and_returns_opaque_ref():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    question = EvidenceQuestion(
        "Timeout failures", entity_refs=("unit:Load_Workorders",),
        time_from=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc), limit=99,
    )
    envelope = await provider.execute(instance=_instance(), source=_source(), credential="secret", capability_id="runtime.events.search", question=question, run_id=7, project_id="project")
    request = transport.calls[-1][2]["json"]
    assert request["size"] == 10
    assert "script" not in str(request)
    assert envelope.items[0].reference.startswith("evref_")
    assert "raw-id" not in str(envelope.items)
    assert "password" not in envelope.items[0].fields


@pytest.mark.asyncio
async def test_search_requires_bounded_time_range():
    provider = ElasticsearchProvider(FakeTransport())
    with pytest.raises(Exception, match="bounded time range"):
        await provider.execute(instance=_instance(), source=_source(), credential="secret", capability_id="runtime.events.search", question=EvidenceQuestion("q"), run_id=7, project_id="project")
