"""Governed client for workspace-owned remote Streamable HTTP MCP servers."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

from ..catalog import connector_catalog
from ..domain import (
    ConnectorBuildState,
    ConnectorCapability,
    ConnectorInstanceConfig,
    ConnectorKind,
    ConnectorProviderManifest,
    ConnectorSourceProfile,
    EvidenceQuestion,
)
from ..errors import ConnectorError
from ..evidence import redact_value
from ..transport import OutboundPolicy, normalize_endpoint, pinned_http_client


REMOTE_MCP_CALL_CAPABILITY = "remote.mcp.call"
_TOOL_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
_MAX_TOOLS = 100
_MAX_TOOL_PAGES = 10
_MAX_SCHEMA_BYTES = 32_768
_MAX_SCHEMA_DEPTH = 12
_MAX_RESULT_BYTES = 131_072
_MAX_CONTENT_BLOCKS = 20


REMOTE_MCP_MANIFEST = ConnectorProviderManifest(
    provider_id="remote_mcp",
    label="Remote MCP Server",
    version="1",
    authentication_types=("none", "bearer", "api_key"),
    configuration_schema={
        "endpoint": "HTTPS Streamable HTTP MCP URL",
        "transport": "streamable_http",
        "verify_tls": "boolean (must remain true)",
    },
    secret_fields=("credential",),
    capabilities=(
        ConnectorCapability(
            id=REMOTE_MCP_CALL_CAPABILITY,
            label="Call reviewed remote MCP tool",
            description="Invoke one owner-reviewed, project-bound read-only MCP tool.",
            read_only=True,
            gather_eligible=True,
            use_when="The selected remote tool is the approved source for the question.",
            do_not_use_when="The tool is unreviewed, changed, disabled, or outside project scope.",
        ),
    ),
    kind=ConnectorKind.LIVE_EVIDENCE,
    build_state=ConnectorBuildState.AVAILABLE,
    description="Connect a standards-compliant remote MCP server over bounded HTTPS.",
    account_key_field="endpoint",
)


class RemoteMcpClientProtocol(Protocol):
    async def list_tools(
        self, instance: ConnectorInstanceConfig, credential: str | None
    ) -> tuple[list[Tool], dict[str, str]]: ...

    async def call_tool(
        self,
        instance: ConnectorInstanceConfig,
        credential: str | None,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        expected_digest: str,
    ) -> CallToolResult: ...


def _clean_text(value: str | None, limit: int) -> str:
    if not value:
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value).strip()[:limit]


def _schema_depth(value: Any, depth: int = 0) -> int:
    if depth > _MAX_SCHEMA_DEPTH:
        return depth
    if isinstance(value, dict):
        return max((_schema_depth(item, depth + 1) for item in value.values()), default=depth)
    if isinstance(value, list):
        return max((_schema_depth(item, depth + 1) for item in value), default=depth)
    return depth


def _validated_schema(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise ConnectorError("unsupported_tool_schema", "Remote MCP tool schema must be an object")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > _MAX_SCHEMA_BYTES or _schema_depth(value) > _MAX_SCHEMA_DEPTH:
        raise ConnectorError("unsupported_tool_schema", "Remote MCP tool schema exceeds safe limits")
    if "$ref" in encoded.decode(errors="ignore"):
        raise ConnectorError("unsupported_tool_schema", "Remote MCP tool schema references are unsupported")
    try:
        Draft202012Validator.check_schema(value)
    except Exception as exc:
        raise ConnectorError("unsupported_tool_schema", "Remote MCP tool schema is invalid") from exc
    return value


def _tool_digest(tool: Tool) -> str:
    schema = _validated_schema(tool.inputSchema)
    annotations = tool.annotations.model_dump(mode="json") if tool.annotations else {}
    payload = {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": schema,
        "annotations": annotations,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _tool_source_id(connector_id: str, tool_name: str) -> str:
    try:
        namespace = uuid.UUID(connector_id)
    except ValueError:
        namespace = uuid.uuid5(uuid.NAMESPACE_URL, connector_id)
    return str(uuid.uuid5(namespace, tool_name))


def _profile(instance: ConnectorInstanceConfig, tool: Tool) -> ConnectorSourceProfile:
    if not _TOOL_NAME.fullmatch(tool.name):
        raise ConnectorError("unsupported_tool", "Remote MCP tool name is invalid")
    schema = _validated_schema(tool.inputSchema)
    digest = _tool_digest(tool)
    annotations = tool.annotations.model_dump(mode="json") if tool.annotations else {}
    return ConnectorSourceProfile(
        id=_tool_source_id(instance.id, tool.name),
        connector_instance_id=instance.id,
        source_type="mcp_tool",
        display_label=_clean_text(tool.title or tool.name, 120) or "Remote MCP tool",
        locator=tool.name,
        fields={
            "profile_version": 1,
            "remote_description": _clean_text(tool.description, 1_000),
            "input_schema": schema,
            "annotations": annotations,
            "schema_digest": digest,
            "review_status": "pending",
        },
        enabled=False,
        discovered_at=dt.datetime.now(dt.timezone.utc),
        discovery_digest=digest,
    )


class RemoteMcpClient:
    def _config(self, instance: ConnectorInstanceConfig) -> tuple[str, OutboundPolicy]:
        endpoint = instance.safe_config.get("endpoint")
        transport = instance.safe_config.get("transport", "streamable_http")
        if not isinstance(endpoint, str):
            raise ConnectorError("invalid_endpoint", "Remote MCP endpoint is required")
        if transport != "streamable_http":
            raise ConnectorError("unsupported_transport", "Only Streamable HTTP MCP is supported")
        normalized = normalize_endpoint(endpoint)
        return normalized, OutboundPolicy(
            require_https=True,
            connect_timeout_seconds=5,
            read_timeout_seconds=15,
            total_timeout_seconds=20,
            max_response_bytes=1_048_576,
            custom_ca_pem=(
                str(instance.safe_config["custom_ca_pem"])
                if instance.safe_config.get("custom_ca_pem")
                else None
            ),
        )

    @staticmethod
    def _headers(instance: ConnectorInstanceConfig, credential: str | None) -> dict[str, str]:
        if instance.credential_type == "none":
            if credential:
                raise ConnectorError("invalid_credentials", "Unauthenticated MCP cannot use a credential")
            return {}
        if not credential or "\r" in credential or "\n" in credential:
            raise ConnectorError("invalid_credentials", "Remote MCP credential is invalid")
        if instance.credential_type == "bearer":
            return {"Authorization": f"Bearer {credential}"}
        if instance.credential_type == "api_key":
            return {"Authorization": f"ApiKey {credential}"}
        raise ConnectorError("invalid_credentials", "Unsupported remote MCP authentication")

    @asynccontextmanager
    async def _session(
        self, instance: ConnectorInstanceConfig, credential: str | None
    ) -> AsyncIterator[ClientSession]:
        endpoint, policy = self._config(instance)
        headers = self._headers(instance, credential)
        try:
            async with asyncio.timeout(policy.total_timeout_seconds):
                async with pinned_http_client(endpoint, policy=policy, headers=headers) as (
                    approved_endpoint,
                    client,
                ):
                    async with streamable_http_client(
                        approved_endpoint,
                        http_client=client,
                        terminate_on_close=True,
                    ) as (read_stream, write_stream, _session_id):
                        async with ClientSession(
                            read_stream,
                            write_stream,
                            read_timeout_seconds=timedelta(seconds=policy.read_timeout_seconds),
                        ) as session:
                            await session.initialize()
                            yield session
        except ConnectorError:
            raise
        except TimeoutError as exc:
            raise ConnectorError(
                "query_timeout", "Remote MCP request timed out", retryable=True
            ) from exc
        except Exception as exc:
            raise ConnectorError(
                "provider_failure", "Remote MCP server could not be reached", retryable=True
            ) from exc

    async def _list_session_tools(self, session: ClientSession) -> list[Tool]:
        tools: list[Tool] = []
        cursor: str | None = None
        for _ in range(_MAX_TOOL_PAGES):
            result = await session.list_tools(cursor=cursor)
            tools.extend(result.tools)
            if len(tools) > _MAX_TOOLS:
                raise ConnectorError("too_many_tools", "Remote MCP server exposes too many tools")
            cursor = result.nextCursor
            if not cursor:
                return tools
        raise ConnectorError("too_many_tools", "Remote MCP tool catalogue is too large")

    async def list_tools(
        self, instance: ConnectorInstanceConfig, credential: str | None
    ) -> tuple[list[Tool], dict[str, str]]:
        async with self._session(instance, credential) as session:
            tools = await self._list_session_tools(session)
            return tools, {"protocol": "streamable_http"}

    async def call_tool(
        self,
        instance: ConnectorInstanceConfig,
        credential: str | None,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        expected_digest: str,
    ) -> CallToolResult:
        async with self._session(instance, credential) as session:
            tools = await self._list_session_tools(session)
            tool = next((item for item in tools if item.name == tool_name), None)
            if tool is None or _tool_digest(tool) != expected_digest:
                raise ConnectorError(
                    "tool_changed", "Remote MCP tool changed and requires owner review"
                )
            schema = _validated_schema(tool.inputSchema)
            error = next(Draft202012Validator(schema).iter_errors(arguments), None)
            if error is not None:
                raise ConnectorError("invalid_tool_arguments", "Remote MCP tool arguments are invalid")
            return await session.call_tool(tool_name, arguments=arguments)


class RemoteMcpProvider:
    manifest = REMOTE_MCP_MANIFEST

    def __init__(self, client: RemoteMcpClientProtocol | None = None) -> None:
        self.client = client or RemoteMcpClient()

    async def test(
        self, instance: ConnectorInstanceConfig, credential: str | None
    ) -> dict[str, object]:
        tools, metadata = await self.client.list_tools(instance, credential)
        return {"status": "connected", "tool_count": len(tools), **metadata}

    async def discover(
        self,
        instance: ConnectorInstanceConfig,
        credential: str | None,
        **_options: object,
    ) -> list[ConnectorSourceProfile]:
        tools, _metadata = await self.client.list_tools(instance, credential)
        profiles: list[ConnectorSourceProfile] = []
        for tool in tools:
            try:
                profiles.append(_profile(instance, tool))
            except ConnectorError as exc:
                if exc.code not in {"unsupported_tool", "unsupported_tool_schema"}:
                    raise
        if tools and not profiles:
            raise ConnectorError(
                "no_compatible_tools", "Remote MCP server has no safely compatible tools"
            )
        return profiles

    async def describe(
        self,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str | None,
    ) -> ConnectorSourceProfile:
        profiles = await self.discover(instance, credential)
        match = next((profile for profile in profiles if profile.id == source.id), None)
        if match is None:
            raise ConnectorError("tool_changed", "Remote MCP tool is no longer available")
        return match

    async def execute(
        self,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str | None,
        capability_id: str,
        question: EvidenceQuestion,
        run_id: int,
        project_id: str,
    ) -> object:
        del instance, source, credential, capability_id, question, run_id, project_id
        raise ConnectorError(
            "unsupported_operation", "Remote MCP tools use the reviewed tool-call endpoint"
        )

    async def call_reviewed_tool(
        self,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str | None,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        digest = source.fields.get("schema_digest")
        if not isinstance(digest, str) or digest != source.discovery_digest:
            raise ConnectorError("tool_changed", "Remote MCP tool requires owner review")
        result = await self.client.call_tool(
            instance,
            credential,
            tool_name=source.locator,
            arguments=arguments,
            expected_digest=digest,
        )
        return _normalize_result(result)


def _normalize_result(result: CallToolResult) -> dict[str, Any]:
    content: list[dict[str, str]] = []
    omitted: list[str] = []
    redaction_count = 0
    truncated = len(result.content) > _MAX_CONTENT_BLOCKS
    for block in result.content[:_MAX_CONTENT_BLOCKS]:
        if block.type != "text":
            omitted.append(block.type)
            continue
        value, count, clipped = redact_value(block.text, max_string_chars=8_000)
        redaction_count += count
        truncated = truncated or clipped
        content.append({"type": "text", "text": str(value)})
    structured: dict[str, Any] | None = None
    if result.structuredContent is not None:
        value, count, clipped = redact_value(result.structuredContent, max_string_chars=8_000)
        redaction_count += count
        truncated = truncated or clipped
        if isinstance(value, dict):
            structured = value
    payload = {
        "is_error": bool(result.isError),
        "content": content,
        "structured_content": structured,
        "redaction_count": redaction_count,
        "truncated": truncated,
        "omitted_content_types": sorted(set(omitted)),
    }
    encoded = json.dumps(payload, separators=(",", ":"), default=str).encode()
    if len(encoded) > _MAX_RESULT_BYTES:
        payload["structured_content"] = None
        payload["content"] = content[:5]
        payload["truncated"] = True
    return payload


def register_remote_mcp_provider() -> None:
    try:
        connector_catalog.manifest(REMOTE_MCP_MANIFEST.provider_id)
    except KeyError:
        connector_catalog.register(REMOTE_MCP_MANIFEST, RemoteMcpProvider())


__all__ = [
    "REMOTE_MCP_CALL_CAPABILITY",
    "REMOTE_MCP_MANIFEST",
    "RemoteMcpClient",
    "RemoteMcpProvider",
    "register_remote_mcp_provider",
]
