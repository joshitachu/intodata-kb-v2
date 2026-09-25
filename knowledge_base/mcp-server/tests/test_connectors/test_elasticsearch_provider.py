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
        if url.endswith("/_field_caps"):
            patterns = kwargs["json"]["fields"]
            if "@timestamp" in patterns:
                return {"fields": {"@timestamp": {"date": {"type": "date", "searchable": True}}}}, 200
            if "*message*" in patterns:
                return {"fields": {
                    "message": {"text": {"type": "text", "searchable": True}},
                    "error.message": {"text": {"type": "text", "searchable": True}},
                    "service.name": {"keyword": {"type": "keyword", "searchable": True}},
                }}, 200
            return {"fields": {"severity": {"keyword": {"type": "keyword", "searchable": True}}}}, 200
        if url.endswith("/_search"):
            if kwargs["json"]["size"] == 0:
                return {"hits": {"total": {"value": 12}}, "aggregations": {
                    "severity": {"buckets": [{"key": "error", "doc_count": 2}]},
                    "latest_event": {"value": 1798135200000, "value_as_string": "2026-12-24T10:00:00.000Z"},
                    "events_in_window": {"doc_count": 3},
                    "timestamped_documents": {"value": 10},
                }}, 200
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
async def test_source_profile_uses_read_permitted_field_capabilities():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    source = await provider.describe(_instance(), _source(), "credential")

    assert source.fields["timestamp_field"] == "@timestamp"
    assert source.fields["message_fields"] == ["error.message", "message"]
    assert source.fields["service_field"] == "service.name"
    assert source.fields["severity_field"] == "severity"
    assert source.fields["search_fields"] == ["error.message", "message", "service.name", "severity"]
    assert source.fields["message_fields"] == ["error.message", "message"]
    assert source.searchable_fields == ("error.message", "message", "service.name", "severity")
    assert "search_fields" not in source.approved_fields
    assert [(method, url) for method, url, _ in transport.calls] == [
        ("POST", "https://elastic.example/logs-prod/_field_caps"),
        ("POST", "https://elastic.example/logs-prod/_field_caps"),
        ("POST", "https://elastic.example/logs-prod/_field_caps"),
        ("POST", "https://elastic.example/logs-prod/_field_caps"),
    ]


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


@pytest.mark.asyncio
async def test_freshness_checks_latest_and_recent_counts_without_returning_documents():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    checked_at = dt.datetime(2026, 12, 24, 12, tzinfo=dt.timezone.utc)

    result = await provider.check_freshness(
        _instance(), _source(), "secret", checked_at=checked_at, window_days=7,
    )

    request = transport.calls[-1][2]["json"]
    assert request["size"] == 0
    assert request["query"] == {"match_all": {}}
    assert request["aggs"]["latest_event"]["max"]["field"] == "@timestamp"
    assert request["aggs"]["events_in_window"]["filter"]["range"]["@timestamp"] == {
        "gte": "2026-12-17T12:00:00+00:00", "lte": "2026-12-24T12:00:00+00:00",
    }
    assert result == {
        "latest_event_at": "2026-12-24T10:00:00.000Z",
        "events_in_window": 3,
        "total_documents": 12,
        "timestamped_documents": 10,
    }


@pytest.mark.asyncio
async def test_wildcard_search_is_a_match_any_event_probe_not_a_literal_term():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    question = EvidenceQuestion(
        "*", entity_refs=("*",), time_from=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc), limit=5,
    )

    await provider.execute(
        instance=_instance(), source=_source(), credential="secret",
        capability_id="runtime.events.search", question=question, run_id=7, project_id="project",
    )

    assert "should" not in transport.calls[-1][2]["json"]["query"]["bool"]


@pytest.mark.asyncio
async def test_or_search_syntax_is_translated_to_alternative_text_matches():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    question = EvidenceQuestion(
        "error OR failed OR exception", entity_refs=("error OR failed OR exception",),
        time_from=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc), limit=5,
    )

    await provider.execute(
        instance=_instance(), source=_source(), credential="secret",
        capability_id="runtime.events.search", question=question, run_id=7, project_id="project",
    )

    clauses = transport.calls[-1][2]["json"]["query"]["bool"]["should"]
    assert [clause["multi_match"]["query"] for clause in clauses] == ["error", "failed", "exception"]
    assert transport.calls[-1][2]["json"]["query"]["bool"]["minimum_should_match"] == 1


@pytest.mark.asyncio
async def test_search_uses_discovered_search_only_fields_without_returning_them():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    source = ConnectorSourceProfile(
        "src", "con", "index", "Production logs", "logs-prod",
        {
            "timestamp_field": "@timestamp",
            "message_fields": ["message"],
            "search_fields": ["error.message", "message", "service.name"],
            "service_field": "service.name",
        },
    )
    question = EvidenceQuestion(
        "exception", entity_refs=("exception",),
        time_from=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc), limit=5,
    )

    envelope = await provider.execute(
        instance=_instance(), source=source, credential="secret",
        capability_id="runtime.events.search", question=question,
        run_id=7, project_id="project",
    )

    request = transport.calls[-1][2]["json"]
    query_fields = request["query"]["bool"]["should"][0]["multi_match"]["fields"]
    assert query_fields == ["error.message", "message", "service.name"]
    assert request["_source"]["includes"] == ["@timestamp", "message", "service.name"]
    assert "search_fields" not in envelope.items[0].fields
