from __future__ import annotations

import json

import httpx
import pytest

from src.harness.connectors.mcp_bridge import McpConnectorBridge
from src.harness.connectors.models import ConnectorInvocationResult
from src.harness.connectors.remote_mcp import RemoteMcpConnector, remote_mcp_config


def _source(*, read_only: bool = True, schema: dict | None = None) -> dict:
    return {
        "id": "tool-source-1",
        "display_label": "Search tickets",
        "field_profile": {
            "tool_name": "search_tickets",
            "description": "Search the approved ticket workspace.",
            "input_schema": schema
            or {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": read_only},
        },
        "discovery_digest": "sha256:abc",
    }


def test_remote_mcp_snapshot_becomes_one_bounded_dynamic_capability() -> None:
    config = remote_mcp_config(
        connector_id="connector-1",
        server_url="https://mcp.example.com/mcp",
        sources=[_source()],
    )
    capability = RemoteMcpConnector().capabilities_for_instance(config)[0]

    assert capability.id.startswith("connector.remote_mcp.tool_")
    assert capability.risk_tier == "read_only"
    assert capability.input_schema["required"] == ["query"]
    assert "untrusted metadata" in (capability.purpose or "")


def test_remote_mcp_tool_defaults_to_effect_risk_without_explicit_read_only_hint() -> None:
    config = remote_mcp_config(
        connector_id="connector-1",
        server_url="https://mcp.example.com/mcp",
        sources=[_source(read_only=False)],
    )
    capability = RemoteMcpConnector().capabilities_for_instance(config)[0]
    assert capability.risk_tier == "external_side_effect"


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string"},
        {"type": "object", "$ref": "https://attacker.example/schema.json"},
        {"type": "object", "properties": {f"p{index}": {} for index in range(101)}},
    ],
)
def test_remote_mcp_rejects_unsafe_or_unbounded_tool_schemas(schema: dict) -> None:
    with pytest.raises(ValueError):
        remote_mcp_config(
            connector_id="connector-1",
            server_url="https://mcp.example.com/mcp",
            sources=[_source(schema=schema)],
        )


@pytest.mark.asyncio
async def test_remote_mcp_invocation_uses_attested_source_id_and_project() -> None:
    class Bridge:
        async def call_remote_mcp_tool(self, access_token: str, **kwargs):
            assert access_token == "session"
            assert kwargs == {
                "project_id": "alpha",
                "connector_id": "connector-1",
                "tool_id": "tool-source-1",
                "arguments": {"query": "broken order"},
            }
            return {
                "content": [{"type": "text", "text": "Ticket 42"}],
                "structured_content": {"ticket": 42},
                "is_error": False,
                "diagnostics": {"content_blocks": 1},
            }

    connector = RemoteMcpConnector(bridge=Bridge())
    config = remote_mcp_config(
        connector_id="connector-1",
        server_url="https://mcp.example.com/mcp",
        sources=[_source()],
    )
    capability = connector.capabilities_for_instance(config)[0]
    result = await connector.invoke_for_user(
        capability.id,
        {"query": "broken order"},
        config,
        project_id="alpha",
        access_token="session",
    )

    assert result == ConnectorInvocationResult(
        content=[{"type": "text", "text": "Ticket 42"}],
        structured_content={"ticket": 42},
        diagnostics={"content_blocks": 1},
    )


@pytest.mark.asyncio
async def test_remote_mcp_bridge_creates_unauthenticated_server_without_fake_secret() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/mcp/connectors":
            return httpx.Response(201, json={"id": "connector-1"})
        if request.url.path == "/mcp/connectors/connector-1/test":
            return httpx.Response(200, json={"status": "connected"})
        if request.url.path == "/mcp/connectors/connector-1/discover":
            return httpx.Response(200, json={"sources": [_source()]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    bridge = McpConnectorBridge(transport=httpx.MockTransport(handler))
    connector, sources = await bridge.create_remote_mcp(
        "session",
        name="Support",
        server_url="https://mcp.example.test/mcp",
        authentication="none",
        credential=None,
    )

    assert connector["id"] == "connector-1"
    assert len(sources) == 1
    create_body = json.loads(requests[0].content)
    assert create_body["credential_type"] == "none"
    assert create_body["credential"] is None


@pytest.mark.asyncio
async def test_remote_mcp_bridge_reviews_and_project_binds_exact_sources() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    bridge = McpConnectorBridge(transport=httpx.MockTransport(handler))
    await bridge.review_remote_mcp_tool(
        "session",
        connector_id="connector-1",
        source_id="source-1",
        enabled=True,
        approved_purpose="Search approved tickets",
    )
    await bridge.bind_remote_mcp_project(
        "session",
        project_id="alpha",
        connector_id="connector-1",
        source_ids=["source-1", "source-1"],
    )

    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/mcp/connectors/connector-1/sources/source-1"
    assert requests[1].method == "PUT"
    assert requests[1].url.path == "/mcp/projects/alpha/connector-bindings/connector-1"
    assert json.loads(requests[1].content)["source_ids"] == ["source-1"]
