from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from src.harness.connectors.models import ConnectorQuery
from src.harness.connectors.openapi_readonly import (
    OPENAPI_SEARCH_CAPABILITY_ID,
    OpenApiReadOnlyConnector,
)


def _config(**overrides):
    return {
        "base_url": "https://evidence.example.com",
        "allowed_hosts": ["evidence.example.com"],
        "openapi_spec": {
            "openapi": "3.0.3",
            "paths": {
                "/v1/search": {
                    "get": {
                        "operationId": "searchEvidence",
                        "parameters": [
                            {"name": name, "in": "query", "required": True}
                            for name in ("q", "project_id", "limit")
                        ],
                    }
                }
            },
        },
        "operation_id": "searchEvidence",
        "operation_path": "/v1/search",
        "health_project_id": "project-alpha",
        "max_results": 2,
        **overrides,
    }


async def _public_host(_host):
    return ("8.8.8.8",)


@pytest.mark.asyncio
async def test_openapi_get_uses_bound_project_and_brokered_credential():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/search"
        assert request.url.params["project_id"] == "project-alpha"
        assert request.url.params["limit"] == "2"
        return httpx.Response(200, json={
            "items": [
                {"title": "Runbook", "excerpt": "Check the queue", "source_ref": "runbook-7", "project_id": "project-alpha"},
                {"title": "Owner", "excerpt": "Platform team", "source_ref": "owner-2", "project_id": "project-alpha"},
            ],
            "has_more": True,
        })

    adapter = OpenApiReadOnlyConnector(
        transport=httpx.MockTransport(handler), host_resolver=_public_host,
        secret_resolver=lambda reference, *, expected_host: "secret-value",
    )
    result = await adapter.read(
        OPENAPI_SEARCH_CAPABILITY_ID, ConnectorQuery(query="queue", limit=50),
        _config(), "env:NEXUS_CONNECTOR_EVIDENCE", "project-alpha",
    )
    assert [item.title for item in result.items] == ["Runbook", "Owner"]
    assert result.items[0].source_ref == "openapi:evidence.example.com:runbook-7"
    assert result.truncated is True
    assert requests[0].url.params["q"] == "queue"
    assert requests[0].headers["authorization"] == "Bearer secret-value"


@pytest.mark.asyncio
async def test_openapi_rejects_unscoped_evidence_and_missing_project():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [
            {"title": "Other", "excerpt": "Wrong project", "source_ref": "bad", "project_id": "project-beta"},
        ]})

    adapter = OpenApiReadOnlyConnector(transport=httpx.MockTransport(handler), host_resolver=_public_host)
    with pytest.raises(ValueError, match="bound project"):
        await adapter.read(OPENAPI_SEARCH_CAPABILITY_ID, ConnectorQuery(query="queue"), _config(), None, None)
    assert requests == []
    with pytest.raises(ValueError, match="unscoped result"):
        await adapter.read(OPENAPI_SEARCH_CAPABILITY_ID, ConnectorQuery(query="queue"), _config(), None, "project-alpha")


@pytest.mark.asyncio
async def test_openapi_health_and_network_limits():
    adapter = OpenApiReadOnlyConnector(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"items": []})),
        host_resolver=_public_host,
    )
    assert (await adapter.health(_config(), None)).status.value == "healthy"

    async def private_host(_host):
        return ("127.0.0.1",)

    private = OpenApiReadOnlyConnector(host_resolver=private_host)
    with pytest.raises(ValueError, match="non-public"):
        await private.health(_config(), None)

    redirect = OpenApiReadOnlyConnector(
        transport=httpx.MockTransport(lambda _request: httpx.Response(302, headers={"location": "https://elsewhere"})),
        host_resolver=_public_host,
    )
    with pytest.raises(ValueError, match="redirect"):
        await redirect.health(_config(), None)

    oversized = OpenApiReadOnlyConnector(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"x" * 1025)),
        host_resolver=_public_host,
    )
    with pytest.raises(ValueError, match="size limit"):
        await oversized.health(_config(max_response_bytes=1024), None)


@pytest.mark.parametrize("path", ["/../admin", "/v1/{id}", "//metadata", "/v1/%2e%2e/secret", "/v1/search?q=a"])
def test_openapi_rejects_dynamic_or_unsafe_paths(path):
    with pytest.raises(ValidationError):
        OpenApiReadOnlyConnector().validate_config(_config(operation_path=path))


def test_openapi_requires_reviewed_get_operation_with_project_parameter():
    base = _config()
    spec = json.loads(json.dumps(base["openapi_spec"]))
    spec["paths"]["/v1/search"]["get"]["parameters"] = [
        {"name": "q", "in": "query", "required": True},
        {"name": "limit", "in": "query", "required": True},
    ]
    with pytest.raises(ValidationError, match="require query, project, and limit"):
        OpenApiReadOnlyConnector().validate_config(_config(openapi_spec=spec))

    spec = json.loads(json.dumps(base["openapi_spec"]))
    spec["paths"]["/v1/search"]["post"] = spec["paths"]["/v1/search"].pop("get")
    with pytest.raises(ValidationError, match="GET operation was not found"):
        OpenApiReadOnlyConnector().validate_config(_config(openapi_spec=spec))

    with pytest.raises(ValidationError, match="explicitly allowlisted"):
        OpenApiReadOnlyConnector().validate_config(_config(allowed_hosts=["other.example.com"]))
