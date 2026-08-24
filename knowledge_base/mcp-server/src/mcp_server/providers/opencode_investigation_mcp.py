"""Run-local MCP boundary for OpenCode investigation evidence.

OpenCode keeps its native read/search tools.  These four tools are only the
typed commit protocol between an exploratory agent and Nexus' durable Gather
state; the model never has to construct or validate legacy investigation files.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections.abc import Mapping
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from mcp_server.providers.opencode_evidence import (
    EvidenceItem,
    FinishScopeRequest,
    GetProgressRequest,
    OpenCodeEvidenceStore,
    ReportGapRequest,
    SubmitEvidenceRequest,
)


class InvestigationFacade:
    """Expose evidence operations for only the scopes assigned to one agent."""

    def __init__(self, store: OpenCodeEvidenceStore, scope_ids: set[str]) -> None:
        self.store = store
        self.scope_ids = frozenset(scope_ids)
        self.token = secrets.token_urlsafe(32)

    @staticmethod
    def _scope_property() -> dict[str, Any]:
        return {"type": "string", "minLength": 1, "maxLength": 240}

    def list_tools(self) -> list[Tool]:
        scope = self._scope_property()
        return [
            Tool(
                name="submit_evidence",
                description=(
                    "Validate and durably save evidence discovered for an assigned scope. "
                    "Submit small batches as you work; accepted evidence survives restarts."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "scope_id": scope,
                        "evidence": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 50,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "unit_name": {"type": "string", "minLength": 1},
                                    "category": {"type": "string", "minLength": 1},
                                    "source_file": {"type": "string"},
                                    "line_range": {"type": "string"},
                                    "chunk_ref": {"type": "string"},
                                    "snippet": {"type": "string"},
                                    "observation": {"type": "string", "minLength": 1},
                                },
                                "required": ["unit_name", "category", "observation"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["scope_id", "evidence"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="report_gap",
                description="Account for an assigned unit that cannot be evidenced, with a concrete reason.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "scope_id": scope,
                        "unit_name": {"type": "string", "minLength": 1},
                        "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
                    },
                    "required": ["scope_id", "unit_name", "reason"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="get_progress",
                description="Return evidenced, gap, and still-unaccounted units for one assigned scope.",
                inputSchema={
                    "type": "object",
                    "properties": {"scope_id": scope},
                    "required": ["scope_id"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="finish_scope",
                description=(
                    "Finalize one scope after every assigned unit has evidence or an explicit gap. "
                    "Nexus writes the validated report; do not write JSON files yourself."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"scope_id": scope},
                    "required": ["scope_id"],
                    "additionalProperties": False,
                },
            ),
        ]

    def _authorize(self, arguments: Mapping[str, Any]) -> str:
        scope_id = arguments.get("scope_id")
        if not isinstance(scope_id, str) or scope_id not in self.scope_ids:
            raise ValueError("scope is not assigned to this session")
        return scope_id

    async def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            scope_id = self._authorize(arguments)
            if name == "submit_evidence":
                evidence = tuple(EvidenceItem.model_validate(item) for item in arguments.get("evidence", ()))
                result = self.store.submit_evidence(
                    SubmitEvidenceRequest(scope_id=scope_id, evidence=evidence)
                )
            elif name == "report_gap":
                result = self.store.report_gap(
                    ReportGapRequest(
                        scope_id=scope_id,
                        unit_name=arguments.get("unit_name"),
                        reason=arguments.get("reason"),
                    )
                )
            elif name == "get_progress":
                result = self.store.get_progress(GetProgressRequest(scope_id=scope_id))
            elif name == "finish_scope":
                result = self.store.finish_scope(FinishScopeRequest(scope_id=scope_id))
            else:
                raise ValueError("unknown investigation tool")
            return result.model_dump(mode="json")
        except Exception as exc:
            # Validation feedback is intentionally returned to the agent so it
            # can correct a submission in the same autonomous run.
            return {"ok": False, "error": type(exc).__name__, "reason": str(exc)[:1000]}


async def _bridge_request(socket_path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    writer.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
    await writer.drain()
    raw = await reader.readline()
    writer.close()
    await writer.wait_closed()
    response = json.loads(raw.decode("utf-8"))
    return response if isinstance(response, dict) else {"error": "invalid_bridge_response"}


def main() -> None:
    """Run the child stdio server against the coordinator's local socket."""

    token = os.environ.get("NEXUS_INVESTIGATION_GRANT")
    socket_path = os.environ.get("NEXUS_INVESTIGATION_SOCKET")
    if not token or not socket_path:
        raise SystemExit("Nexus investigation facade is not available")
    server = Server("nexus-opencode-investigation")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        response = await _bridge_request(socket_path, {"token": token, "method": "list_tools"})
        raw_tools = response.get("tools")
        if not isinstance(raw_tools, list):
            return []
        return [
            Tool(name=item["name"], description=item["description"], inputSchema=item["inputSchema"])
            for item in raw_tools
            if isinstance(item, dict)
            and all(key in item for key in ("name", "description", "inputSchema"))
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        response = await _bridge_request(
            socket_path,
            {"token": token, "method": "call", "name": name, "arguments": arguments},
        )
        result = response if response.get("error") else response.get("result")
        return [TextContent(type="text", text=json.dumps(result, separators=(",", ":"), default=str))]

    from mcp.server.stdio import stdio_server

    async def serve() -> None:
        async with stdio_server() as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover
    main()
