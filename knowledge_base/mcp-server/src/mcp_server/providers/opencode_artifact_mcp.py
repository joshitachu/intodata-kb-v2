"""Durable, contract-scoped artifact submission for OpenCode phases.

Analyze and Produce remain agentic read/search sessions, but they do not edit
the shared staging workspace directly.  The model submits a completed artifact
through this local MCP boundary; Nexus authorizes the exact contract target,
performs basic payload validation, and atomically commits the file.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from mcp.server import Server
from mcp.types import TextContent, Tool

_MAX_ARTIFACT_CHARS = 200_000


class ArtifactContract(Protocol):
    """The contract fields required by the submission boundary."""

    target: str
    output_artifact: str


class ArtifactSubmissionFacade:
    """Accept writes only for artifacts declared by the current phase step."""

    def __init__(
        self,
        *,
        staging_dir: Path,
        phase: str,
        step: str,
        contracts: Sequence[ArtifactContract],
    ) -> None:
        self.staging_dir = staging_dir.resolve()
        self.phase = phase
        self.step = step
        self.contracts = {contract.output_artifact: contract for contract in contracts}
        self.token = secrets.token_urlsafe(32)

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="submit_artifact",
                description=(
                    "Validate and atomically save one completed Analyze or Produce artifact. "
                    "The output path must exactly match a contract assigned to this session. "
                    "Use this instead of write, edit, apply_patch, or shell redirection."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "output_artifact": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                        },
                        "content": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAX_ARTIFACT_CHARS,
                        },
                    },
                    "required": ["output_artifact", "content"],
                    "additionalProperties": False,
                },
            )
        ]

    def _audit(self, payload: dict[str, Any]) -> None:
        path = self.staging_dir / "_audit" / "opencode_artifact_submissions.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {"phase": self.phase, "step": self.step, **payload}
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(record, separators=(",", ":"), default=str) + "\n"
                )
        except OSError:
            # The artifact commit is authoritative; audit I/O must not turn an
            # accepted submission into a false rejection.
            pass

    def _resolve_contract(self, output_artifact: Any) -> tuple[ArtifactContract, Path]:
        if not isinstance(output_artifact, str):
            raise TypeError("output_artifact must be a string")
        contract = self.contracts.get(output_artifact)
        if contract is None:
            raise ValueError("output_artifact is not assigned to this session")
        output = (self.staging_dir / contract.output_artifact).resolve()
        if self.staging_dir not in output.parents:
            raise ValueError("output_artifact resolves outside the staging workspace")
        return contract, output

    @staticmethod
    def _validate_content(output: Path, content: Any) -> str:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        if len(content) > _MAX_ARTIFACT_CHARS:
            raise ValueError("content exceeds the artifact character budget")
        if "\x00" in content:
            raise ValueError("content contains a null byte")
        if output.suffix.lower() == ".json":
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON artifact is invalid: {exc.msg}") from exc
            if not isinstance(parsed, (dict, list)):
                raise ValueError("JSON artifact must contain an object or array")
        return content

    @staticmethod
    def _atomic_write(output: Path, content: str) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=output.parent,
            prefix=f".{output.name}.nexus-submit-",
        )
        temp_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, output)
        finally:
            temp_path.unlink(missing_ok=True)

    async def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        output_artifact = arguments.get("output_artifact") if isinstance(arguments, Mapping) else None
        try:
            if name != "submit_artifact":
                raise ValueError("unknown artifact submission tool")
            if not isinstance(arguments, Mapping):
                raise TypeError("tool arguments must be an object")
            unknown = set(arguments) - {"output_artifact", "content"}
            if unknown:
                raise ValueError("unsupported artifact submission input")
            contract, output = self._resolve_contract(output_artifact)
            content = self._validate_content(output, arguments.get("content"))
            self._atomic_write(output, content)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            result = {
                "ok": True,
                "target": contract.target,
                "output_artifact": contract.output_artifact,
                "bytes_written": len(content.encode("utf-8")),
                "sha256": digest,
            }
            self._audit({"status": "accepted", **result})
            return result
        except (OSError, TypeError, ValueError) as exc:
            result = {
                "ok": False,
                "output_artifact": output_artifact if isinstance(output_artifact, str) else None,
                "error": type(exc).__name__,
                "reason": str(exc)[:1000],
            }
            self._audit({"status": "rejected", **result})
            return result


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

    token = os.environ.get("NEXUS_ARTIFACT_GRANT")
    socket_path = os.environ.get("NEXUS_ARTIFACT_SOCKET")
    if not token or not socket_path:
        raise SystemExit("Nexus artifact submission facade is not available")
    server = Server("nexus-opencode-artifacts")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        response = await _bridge_request(
            socket_path,
            {"token": token, "method": "list_tools"},
        )
        raw_tools = response.get("tools")
        if not isinstance(raw_tools, list):
            return []
        return [
            Tool(
                name=item["name"],
                description=item["description"],
                inputSchema=item["inputSchema"],
            )
            for item in raw_tools
            if isinstance(item, dict)
            and all(key in item for key in ("name", "description", "inputSchema"))
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        response = await _bridge_request(
            socket_path,
            {
                "token": token,
                "method": "call",
                "name": name,
                "arguments": arguments,
            },
        )
        result = response if response.get("error") else response.get("result")
        return [
            TextContent(
                type="text",
                text=json.dumps(result, separators=(",", ":"), default=str),
            )
        ]

    from mcp.server.stdio import stdio_server

    async def serve() -> None:
        async with stdio_server() as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )

    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover
    main()
