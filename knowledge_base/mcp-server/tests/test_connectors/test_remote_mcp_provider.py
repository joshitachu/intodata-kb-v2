from __future__ import annotations

from dataclasses import replace

from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
import pytest

from mcp_server.connectors.domain import ConnectorInstanceConfig
from mcp_server.connectors.errors import ConnectorError
from mcp_server.connectors.providers.remote_mcp import RemoteMcpProvider


def _tool(*, description: str = "Search approved tickets") -> Tool:
    return Tool(
        name="search_tickets",
        title="Search tickets",
        description=description,
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        annotations=ToolAnnotations(readOnlyHint=True),
    )


def _instance() -> ConnectorInstanceConfig:
    return ConnectorInstanceConfig(
        id="00000000-0000-0000-0000-000000000001",
        provider_id="remote_mcp",
        display_name="Support MCP",
        safe_config={"endpoint": "https://mcp.example.test/mcp", "transport": "streamable_http"},
        credential_type="none",
    )


class FakeClient:
    def __init__(self) -> None:
        self.tool = _tool()

    async def list_tools(self, instance, credential):
        assert instance.id == _instance().id
        assert credential is None
        return [self.tool], {"protocol": "streamable_http"}

    async def call_tool(self, instance, credential, *, tool_name, arguments, expected_digest):
        assert tool_name == "search_tickets"
        assert arguments == {"query": "broken order"}
        assert expected_digest.startswith("sha256:")
        return CallToolResult(
            content=[TextContent(type="text", text="Ticket 42")],
            structuredContent={"ticket": 42},
            isError=False,
        )


@pytest.mark.asyncio
async def test_discovery_is_disabled_until_owner_review() -> None:
    provider = RemoteMcpProvider(client=FakeClient())

    profiles = await provider.discover(_instance(), None)

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.locator == "search_tickets"
    assert profile.enabled is False
    assert profile.fields["review_status"] == "pending"
    assert profile.fields["annotations"]["readOnlyHint"] is True
    assert profile.discovery_digest == profile.fields["schema_digest"]


@pytest.mark.asyncio
async def test_reviewed_call_is_bounded_and_uses_attested_tool() -> None:
    provider = RemoteMcpProvider(client=FakeClient())
    source = (await provider.discover(_instance(), None))[0]

    result = await provider.call_reviewed_tool(
        _instance(), source, None, {"query": "broken order"}
    )

    assert result["content"] == [{"type": "text", "text": "Ticket 42"}]
    assert result["structured_content"] == {"ticket": 42}
    assert result["is_error"] is False


@pytest.mark.asyncio
async def test_schema_change_invalidates_attestation() -> None:
    provider = RemoteMcpProvider(client=FakeClient())
    source = (await provider.discover(_instance(), None))[0]
    changed = replace(
        source,
        fields={**source.fields, "schema_digest": "sha256:changed"},
    )

    with pytest.raises(ConnectorError) as error:
        await provider.call_reviewed_tool(
            _instance(), changed, None, {"query": "broken order"}
        )
    assert error.value.code == "tool_changed"
