"""A minimal, run-scoped MCP facade over :mod:`mcp_server.connectors.broker`.

The model receives semantic inputs only.  This module intentionally has no
provider endpoint, credential, index name, raw query, header, or arbitrary
HTTP input.  It is suitable for stdio MCP registration as well as focused unit
tests through :class:`RunScopedMcpFacade`.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from mcp_server.connectors.broker import BrokerDenied, CapabilityBroker

_CAPABILITY_TO_TOOL = {
    "evidence.sources.describe": "describe_evidence_source",
    "runtime.events.aggregate": "aggregate_runtime_events",
    "runtime.events.search": "search_runtime_events",
    "runtime.events.get_item": "get_evidence_item",
}
_TOOL_TO_CAPABILITY = {tool: capability for capability, tool in _CAPABILITY_TO_TOOL.items()}


class FacadeInputError(ValueError):
    """Tool input is outside the semantic MCP schema."""


def _schema_for(capability_id: str) -> dict[str, Any]:
    common_source = {"source_id": {"type": "string", "minLength": 1, "maxLength": 200}}
    if capability_id == "evidence.sources.describe":
        return {"type": "object", "properties": common_source, "required": ["source_id"], "additionalProperties": False}
    if capability_id == "runtime.events.get_item":
        return {
            "type": "object",
            "properties": {"evidence_ref": {"type": "string", "pattern": "^evref_[A-Za-z0-9_-]+$", "maxLength": 200}},
            "required": ["evidence_ref"], "additionalProperties": False,
        }
    properties = {
        **common_source,
        "question": {"type": "string", "minLength": 1, "maxLength": 500},
        "entity_refs": {"type": "array", "items": {"type": "string", "maxLength": 200}, "maxItems": 25},
        "time_window": {
            "type": "object",
            "properties": {"from": {"type": "string", "maxLength": 40}, "to": {"type": "string", "maxLength": 40}},
            "required": ["from", "to"], "additionalProperties": False,
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
    }
    if capability_id == "runtime.events.aggregate":
        properties["group_by"] = {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 5}
    else:
        properties["severities"] = {"type": "array", "items": {"type": "string", "maxLength": 50}, "maxItems": 10}
    return {"type": "object", "properties": properties, "required": ["source_id", "question", "time_window"], "additionalProperties": False}


def _validate_input(capability_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        raise FacadeInputError("tool arguments must be an object")
    allowed = set(_schema_for(capability_id)["properties"])
    unknown = set(arguments) - allowed
    if unknown:
        raise FacadeInputError("unsupported semantic input")
    data = dict(arguments)
    if capability_id == "runtime.events.get_item":
        ref = data.get("evidence_ref")
        if not isinstance(ref, str) or not ref.startswith("evref_") or len(ref) > 200:
            raise FacadeInputError("evidence_ref must be an opaque evidence reference")
        return data
    source_id = data.get("source_id")
    if not isinstance(source_id, str) or not source_id or len(source_id) > 200:
        raise FacadeInputError("source_id is required")
    if capability_id == "evidence.sources.describe":
        return data
    question = data.get("question")
    window = data.get("time_window")
    if not isinstance(question, str) or not question.strip() or len(question) > 500:
        raise FacadeInputError("question is required and bounded")
    if not isinstance(window, Mapping) or set(window) != {"from", "to"} or not all(
        isinstance(window.get(key), str) and window[key].strip() for key in ("from", "to")
    ):
        raise FacadeInputError("time_window requires bounded from and to values")
    limit = data.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 25):
        raise FacadeInputError("limit must be between 1 and 25")
    for list_key, ceiling in (("entity_refs", 25), ("group_by", 5), ("severities", 10)):
        value = data.get(list_key)
        if value is not None and (
            not isinstance(value, list) or len(value) > ceiling or not all(isinstance(item, str) and len(item) <= 200 for item in value)
        ):
            raise FacadeInputError(f"{list_key} must be a bounded string list")
    return data


class RunScopedMcpFacade:
    """Expose only the current grant's trusted semantic tools."""

    def __init__(
        self,
        *,
        broker: CapabilityBroker,
        token: str,
        run_id: int | str,
        project_id: str,
        phase: str = "gather",
        facade_nonce: str | None = None,
    ) -> None:
        self.broker = broker
        self.token = token
        self.run_id = run_id
        self.project_id = project_id
        self.phase = phase
        self.facade_nonce = facade_nonce or secrets.token_urlsafe(24)

    def list_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        for capability_id in self.broker.tool_catalogue(self.token):
            name = _CAPABILITY_TO_TOOL.get(capability_id)
            if name is None:
                continue
            tools.append(Tool(
                name=name,
                description="Bounded, read-only runtime evidence. Provider details are unavailable.",
                inputSchema=_schema_for(capability_id),
            ))
        return tools

    async def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        capability_id = _TOOL_TO_CAPABILITY.get(name)
        if capability_id is None or capability_id not in self.broker.tool_catalogue(self.token):
            raise BrokerDenied("tool is not available to this run")
        data = _validate_input(capability_id, arguments)
        source_id = str(data.get("source_id") or "")
        if capability_id == "runtime.events.get_item":
            # A provider resolves opaque references server-side.  It must not accept
            # index/document IDs from the model, and owns the source association.
            resolver = getattr(self.broker, "source_for_evidence_ref", None)
            if not callable(resolver):
                raise BrokerDenied("opaque evidence references are unavailable for this run")
            source_id = resolver(self.token, str(data["evidence_ref"]))
            if not isinstance(source_id, str) or not source_id:
                raise BrokerDenied("unknown evidence reference")
        return await self.broker.execute(
            self.token,
            facade_nonce=self.facade_nonce,
            run_id=self.run_id,
            project_id=self.project_id,
            phase=self.phase,
            capability_id=capability_id,
            source_id=source_id,
            request=data,
        )


class FacadeBridge:
    """Local Unix-socket relay from the spawned stdio facade to the run broker.

    OpenCode launches MCP servers in a child process, so it cannot share the
    generation coordinator's in-memory grant store directly.  The socket lives
    in the per-invocation 0700 config directory and disappears before Analyze.
    """

    def __init__(self, facade: RunScopedMcpFacade, socket_path: Path) -> None:
        self.facade = facade
        # macOS permits only ~104 bytes for AF_UNIX paths.  The generated
        # OpenCode config directory can be longer than that, so use an
        # unguessable 0600 socket under the system temp root when needed.
        if len(os.fsencode(str(socket_path))) > 80:
            socket_path = Path("/private/tmp") / f"nx-evidence-{secrets.token_hex(12)}.sock"
        self.socket_path = socket_path
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(
            self._handle,
            path=str(self.socket_path),
            limit=1024 * 1024,
        )
        self.socket_path.chmod(0o600)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        self.socket_path.unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.readline()
            # Artifact submissions can contain a complete Markdown document or
            # topology JSON. Keep the relay bounded while leaving room for the
            # facade's 200k-character payload plus JSON/UTF-8 overhead.
            if len(raw) > 1024 * 1024:
                raise FacadeInputError("facade request exceeds byte limit")
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise FacadeInputError("facade request must be an object")
            if request.get("token") != self.facade.token:
                raise BrokerDenied("facade token is invalid")
            if request.get("method") == "list_tools":
                response: dict[str, Any] = {"tools": [
                    {"name": tool.name, "description": tool.description, "inputSchema": tool.inputSchema}
                    for tool in self.facade.list_tools()
                ]}
            elif request.get("method") == "call":
                response = {"result": await self.facade.call(
                    str(request.get("name") or ""), request.get("arguments") or {}
                )}
            else:
                raise FacadeInputError("unknown facade method")
        except (BrokerDenied, FacadeInputError, json.JSONDecodeError) as exc:
            response = {"error": "connector_denied", "reason": str(exc)[:200]}
        except Exception:  # noqa: BLE001 - sanitize arbitrary provider failures
            # Provider failures can contain vendor URLs or response fragments;
            # the MCP boundary exposes only a stable limitation code.
            response = {"error": "connector_unavailable", "reason": "provider_failure"}
        writer.write(json.dumps(response, separators=(",", ":"), default=str).encode("utf-8") + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()


def create_mcp_server(facade: RunScopedMcpFacade) -> Server:
    """Create a stdio-compatible MCP server with no global tool catalogue."""

    server = Server("nexus-run-scoped-evidence")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return facade.list_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = await facade.call(name, arguments)
            return [TextContent(type="text", text=json.dumps(result, separators=(",", ":")))]
        except (BrokerDenied, FacadeInputError) as exc:
            return [TextContent(type="text", text=json.dumps({"error": "connector_denied", "reason": str(exc)[:200]}))]

    return server


async def _bridge_request(socket_path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    writer.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
    await writer.drain()
    raw = await reader.readline()
    writer.close()
    await writer.wait_closed()
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict):
        raise BrokerDenied("invalid facade bridge response")
    return result


def main() -> None:
    """Run the child stdio MCP server against an already-authorized local relay."""

    token = os.environ.get("NEXUS_CONNECTOR_GRANT")
    socket_path = os.environ.get("NEXUS_CONNECTOR_SOCKET")
    if not token or not socket_path:
        raise SystemExit("Nexus run-scoped connector facade is not available")
    server = Server("nexus-run-scoped-evidence")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        response = await _bridge_request(socket_path, {"token": token, "method": "list_tools"})
        if response.get("error"):
            return []
        raw_tools = response.get("tools")
        if not isinstance(raw_tools, list):
            return []
        return [Tool(name=item["name"], description=item["description"], inputSchema=item["inputSchema"])
                for item in raw_tools if isinstance(item, dict) and all(key in item for key in ("name", "description", "inputSchema"))]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        response = await _bridge_request(socket_path, {
            "token": token, "method": "call", "name": name, "arguments": arguments,
        })
        if response.get("error"):
            return [TextContent(type="text", text=json.dumps(response, separators=(",", ":")))]
        return [TextContent(type="text", text=json.dumps(response.get("result"), separators=(",", ":"), default=str))]

    from mcp.server.stdio import stdio_server

    async def serve() -> None:
        async with stdio_server() as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover - deployment entrypoint
    main()
