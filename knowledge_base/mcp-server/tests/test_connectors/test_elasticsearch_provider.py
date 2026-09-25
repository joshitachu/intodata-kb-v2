import datetime as dt

import pytest

from mcp_server.connectors.domain import (
    ConnectorInstanceConfig,
    ConnectorSourceProfile,
    EvidenceQuestion,
)
from mcp_server.connectors.errors import ConnectorError
from mcp_server.connectors.providers.elasticsearch import ElasticsearchProvider, _freshness_status


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
                return {
                    "fields": {"@timestamp": {"date": {"type": "date", "searchable": True}}}
                }, 200
            if "*message*" in patterns:
                return {
                    "fields": {
                        "message": {"text": {"type": "text", "searchable": True}},
                        "error.message": {"text": {"type": "text", "searchable": True}},
                        "error.message.keyword": {
                            "keyword": {"type": "keyword", "searchable": True}
                        },
                        "opaque_blob": {"keyword": {"type": "keyword", "searchable": False}},
                    }
                }, 200
            if "*service*" in patterns:
                return {
                    "fields": {
                        "service.name": {"keyword": {"type": "keyword", "searchable": True}},
                        "job.name": {"keyword": {"type": "keyword", "searchable": True}},
                        "log.level": {"keyword": {"type": "keyword", "searchable": True}},
                        "service.environment": {"keyword": {"type": "keyword", "searchable": True}},
                    }
                }, 200
            return {
                "fields": {
                    "log.original": {
                        "match_only_text": {"type": "match_only_text", "searchable": True}
                    },
                    "trace.id": {"keyword": {"type": "keyword", "searchable": True}},
                }
            }, 200
        if url.endswith("/_search"):
            if kwargs["json"]["size"] == 0:
                return {
                    "hits": {"total": {"value": 12}},
                    "aggregations": {
                        "severity": {"buckets": [{"key": "error", "doc_count": 2}]},
                        "latest_event": {
                            "value": 1798135200000,
                            "value_as_string": "2026-12-24T10:00:00.000Z",
                        },
                        "events_in_window": {"doc_count": 3},
                        "timestamped_documents": {"value": 10},
                    },
                }, 200
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "raw-id",
                            "_index": "logs-prod",
                            "_source": {
                                "@timestamp": "2026-08-20T00:00:00Z",
                                "message": "timeout",
                                "service.name": "worker",
                                "password": "ignored",
                            },
                        }
                    ]
                }
            }, 200
        raise AssertionError(url)


def _instance():
    return ConnectorInstanceConfig(
        "con",
        "elastic_stack",
        "Elastic",
        {"endpoint": "https://elastic.example", "auth_type": "api_key"},
    )


def _source():
    return ConnectorSourceProfile(
        "src",
        "con",
        "index",
        "Production logs",
        "logs-prod",
        {
            "timestamp_field": "@timestamp",
            "message_fields": ["message"],
            "service_field": "service.name",
            "severity_field": "severity",
            "max_time_window_days": 30,
            "samples_allowed": True,
        },
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
    assert source.fields["severity_field"] == "log.level"
    assert source.fields["environment_field"] == "service.environment"
    assert source.fields["context_fields"] == ["job.name", "trace.id"]
    assert source.fields["search_fields"] == [
        "error.message",
        "message",
        "service.name",
        "log.level",
        "service.environment",
        "job.name",
        "trace.id",
        "log.original",
    ]
    assert source.searchable_fields == (
        "error.message",
        "message",
        "service.name",
        "log.level",
        "service.environment",
        "job.name",
        "trace.id",
        "log.original",
    )
    assert "error.message.keyword" not in source.searchable_fields
    assert "opaque_blob" not in source.searchable_fields
    assert "search_fields" not in source.approved_fields
    assert [(method, url) for method, url, _ in transport.calls] == [
        ("POST", "https://elastic.example/logs-prod/_field_caps"),
        ("POST", "https://elastic.example/logs-prod/_field_caps"),
        ("POST", "https://elastic.example/logs-prod/_field_caps"),
        ("POST", "https://elastic.example/logs-prod/_field_caps"),
    ]


class SearchOnlyTransport(FakeTransport):
    async def request_json(self, method, url, **kwargs):
        if url.endswith("/_field_caps"):
            self.calls.append((method, url, kwargs))
            raise ConnectorError("provider_request_failed", "Provider request failed")
        if url.endswith("/_search") and kwargs["json"]["size"] == 5:
            self.calls.append((method, url, kwargs))
            return {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2026-09-25T05:00:01.777Z",
                                "LogData": {
                                    "Message": "failed to call https://user:secret@example.test"
                                },
                                "ServiceName": "maximo-fourps",
                                "LogLevel": "ERROR",
                                "Environment": "PROD",
                            }
                        }
                    ]
                }
            }, 200
        return await super().request_json(method, url, **kwargs)


@pytest.mark.asyncio
async def test_source_profile_falls_back_to_bounded_sample_when_field_caps_is_denied():
    transport = SearchOnlyTransport()
    provider = ElasticsearchProvider(transport)
    legacy = ConnectorSourceProfile(
        "src",
        "con",
        "index",
        "Production logs",
        "logs-prod",
        {
            "timestamp_field": "@timestamp",
            "message_fields": ["LogData.Message.keyword"],
        },
    )

    source = await provider.describe(_instance(), legacy, "credential")

    assert source.fields["profile_version"] == 2
    assert source.fields["timestamp_field"] == "@timestamp"
    assert source.fields["message_fields"] == ["LogData.Message"]
    assert "LogData.Message" in source.fields["search_fields"]
    assert source.fields["service_field"] == "ServiceName"
    assert source.fields["severity_field"] == "LogLevel"
    assert source.fields["environment_field"] == "Environment"
    assert "secret" not in str(source.fields)
    assert [url.rsplit("/", 1)[-1] for _, url, _ in transport.calls] == [
        "_field_caps",
        "_search",
    ]
    sample_request = transport.calls[-1][2]["json"]
    assert sample_request["size"] == 5
    assert sample_request["track_total_hits"] is False
    assert sample_request["query"] == {"match_all": {}}


@pytest.mark.asyncio
async def test_search_builds_server_side_bounded_query_and_returns_opaque_ref():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    question = EvidenceQuestion(
        "Timeout failures",
        entity_refs=("unit:Load_Workorders",),
        time_from=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc),
        limit=99,
    )
    envelope = await provider.execute(
        instance=_instance(),
        source=_source(),
        credential="secret",
        capability_id="runtime.events.search",
        question=question,
        run_id=7,
        project_id="project",
    )
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
        await provider.execute(
            instance=_instance(),
            source=_source(),
            credential="secret",
            capability_id="runtime.events.search",
            question=EvidenceQuestion("q"),
            run_id=7,
            project_id="project",
        )


@pytest.mark.asyncio
async def test_freshness_checks_latest_and_recent_counts_without_returning_documents():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    checked_at = dt.datetime(2026, 12, 24, 12, tzinfo=dt.timezone.utc)

    result = await provider.check_freshness(
        _instance(),
        _source(),
        "secret",
        checked_at=checked_at,
        window_days=7,
    )

    request = transport.calls[-1][2]["json"]
    assert request["size"] == 0
    assert request["query"] == {"match_all": {}}
    assert request["aggs"]["latest_event"]["max"]["field"] == "@timestamp"
    assert request["aggs"]["events_in_window"]["filter"]["range"]["@timestamp"] == {
        "gte": "2026-12-17T12:00:00+00:00",
        "lte": "2026-12-24T12:00:00+00:00",
    }
    assert result == {
        "latest_event_at": "2026-12-24T10:00:00.000Z",
        "events_in_window": 3,
        "total_documents": 12,
        "timestamped_documents": 10,
        "freshness_status": "active",
    }


@pytest.mark.parametrize(
    ("events_in_window", "total_documents", "timestamped_documents", "expected"),
    [
        (2, 20, 20, "active"),
        (0, 20, 20, "stale"),
        (0, 20, 0, "timestamp_unavailable"),
        (0, 0, 0, "empty"),
    ],
)
def test_freshness_status_distinguishes_active_stale_and_unobservable_sources(
    events_in_window,
    total_documents,
    timestamped_documents,
    expected,
):
    assert (
        _freshness_status(
            events_in_window=events_in_window,
            total_documents=total_documents,
            timestamped_documents=timestamped_documents,
        )
        == expected
    )


@pytest.mark.asyncio
async def test_wildcard_search_is_a_match_any_event_probe_not_a_literal_term():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    question = EvidenceQuestion(
        "*",
        entity_refs=("*",),
        time_from=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc),
        limit=5,
    )

    await provider.execute(
        instance=_instance(),
        source=_source(),
        credential="secret",
        capability_id="runtime.events.search",
        question=question,
        run_id=7,
        project_id="project",
    )

    assert "should" not in transport.calls[-1][2]["json"]["query"]["bool"]


@pytest.mark.asyncio
async def test_or_search_syntax_is_translated_to_alternative_text_matches():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    question = EvidenceQuestion(
        "error OR failed OR exception",
        entity_refs=("error OR failed OR exception",),
        time_from=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc),
        limit=5,
    )

    await provider.execute(
        instance=_instance(),
        source=_source(),
        credential="secret",
        capability_id="runtime.events.search",
        question=question,
        run_id=7,
        project_id="project",
    )

    clauses = transport.calls[-1][2]["json"]["query"]["bool"]["should"]
    assert [clause["multi_match"]["query"] for clause in clauses] == [
        "error",
        "failed",
        "exception",
    ]
    assert transport.calls[-1][2]["json"]["query"]["bool"]["minimum_should_match"] == 1


@pytest.mark.asyncio
async def test_search_uses_discovered_search_only_fields_without_returning_them():
    transport = FakeTransport()
    provider = ElasticsearchProvider(transport)
    source = ConnectorSourceProfile(
        "src",
        "con",
        "index",
        "Production logs",
        "logs-prod",
        {
            "timestamp_field": "@timestamp",
            "message_fields": ["message"],
            "search_fields": ["error.message", "message", "service.name"],
            "service_field": "service.name",
        },
    )
    question = EvidenceQuestion(
        "exception",
        entity_refs=("exception",),
        time_from=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc),
        limit=5,
    )

    envelope = await provider.execute(
        instance=_instance(),
        source=source,
        credential="secret",
        capability_id="runtime.events.search",
        question=question,
        run_id=7,
        project_id="project",
    )

    request = transport.calls[-1][2]["json"]
    query_fields = request["query"]["bool"]["should"][0]["multi_match"]["fields"]
    assert query_fields == ["error.message", "message", "service.name"]
    assert request["_source"]["includes"] == ["@timestamp", "message", "service.name"]
    assert "search_fields" not in envelope.items[0].fields


class NestedEventTransport(FakeTransport):
    async def request_json(self, method, url, **kwargs):
        if url.endswith("/_search") and kwargs["json"]["size"] > 0:
            self.calls.append((method, url, kwargs))
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "nested-id",
                            "_index": "logs-prod",
                            "_source": {
                                "event": {"created": "2026-08-20T01:02:03Z"},
                                "error": {
                                    "message": "Request failed with Bearer abcdefghijklmnopqrstuvwxyz0123456789",
                                },
                                "log": {
                                    "level": "ERROR",
                                    "original": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
                                },
                                "service": {"name": "maximo-fourps", "environment": "prod"},
                                "job": {"name": "workorder"},
                                "trace": {"id": "trace-123"},
                                "unapproved": {"customer_name": "must not leave the connector"},
                            },
                        }
                    ]
                }
            }, 200
        return await super().request_json(method, url, **kwargs)


@pytest.mark.asyncio
async def test_search_projects_nested_discovered_fields_and_redacts_values():
    transport = NestedEventTransport()
    provider = ElasticsearchProvider(transport)
    source = ConnectorSourceProfile(
        "src",
        "con",
        "index",
        "Production logs",
        "logs-prod",
        {
            "timestamp_field": "event.created",
            "message_fields": ["error.message", "log.original"],
            "search_fields": ["error.message", "log.original", "service.name", "job.name"],
            "service_field": "service.name",
            "severity_field": "log.level",
            "environment_field": "service.environment",
            "context_fields": ["job.name", "trace.id"],
            "max_time_window_days": 30,
            "samples_allowed": True,
        },
    )
    question = EvidenceQuestion(
        "failed",
        entity_refs=("failed",),
        time_from=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 8, 20, 2, tzinfo=dt.timezone.utc),
        limit=5,
    )

    envelope = await provider.execute(
        instance=_instance(),
        source=source,
        credential="secret",
        capability_id="runtime.events.search",
        question=question,
        run_id=7,
        project_id="project",
    )

    item = envelope.items[0]
    assert item.observed_at == dt.datetime(2026, 8, 20, 1, 2, 3, tzinfo=dt.timezone.utc)
    assert item.fields == {
        "event.created": "2026-08-20T01:02:03Z",
        "error.message": "Request failed with [REDACTED]",
        "service.name": "maximo-fourps",
        "log.level": "ERROR",
        "service.environment": "prod",
        "job.name": "workorder",
        "trace.id": "trace-123",
    }
    assert "log.original" not in item.fields
    assert "customer_name" not in str(item.fields)
