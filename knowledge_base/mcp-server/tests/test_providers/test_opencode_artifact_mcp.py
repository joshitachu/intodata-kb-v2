from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_server.connectors.mcp_facade import FacadeBridge
from mcp_server.providers.opencode_artifact_mcp import ArtifactSubmissionFacade


def _facade(tmp_path: Path, *, output: str = "_analysis/Group.md") -> ArtifactSubmissionFacade:
    return ArtifactSubmissionFacade(
        staging_dir=tmp_path,
        phase="analyze",
        step="analyze_batch_001",
        contracts=[SimpleNamespace(target="analysis:Group", output_artifact=output)],
    )


def test_artifact_facade_atomically_commits_only_assigned_output(tmp_path: Path) -> None:
    facade = _facade(tmp_path)

    async def exercise() -> None:
        accepted = await facade.call(
            "submit_artifact",
            {
                "output_artifact": "_analysis/Group.md",
                "content": "# Group analysis\n\nSupported content.\n",
            },
        )
        assert accepted["ok"] is True
        assert accepted["target"] == "analysis:Group"
        assert accepted["bytes_written"] > 0

        rejected = await facade.call(
            "submit_artifact",
            {
                "output_artifact": "_analysis/Other.md",
                "content": "# Unauthorized\n",
            },
        )
        assert rejected["ok"] is False
        assert "not assigned" in rejected["reason"]

    asyncio.run(exercise())

    assert (tmp_path / "_analysis/Group.md").read_text(encoding="utf-8") == (
        "# Group analysis\n\nSupported content.\n"
    )
    assert not (tmp_path / "_analysis/Other.md").exists()
    assert not list((tmp_path / "_analysis").glob("*.nexus-submit-*"))


def test_artifact_facade_returns_actionable_validation_without_leaking_content(
    tmp_path: Path,
) -> None:
    facade = _facade(tmp_path, output="_topology/business_topology.json")
    secret_marker = "not-json-secret-marker"

    result = asyncio.run(
        facade.call(
            "submit_artifact",
            {
                "output_artifact": "_topology/business_topology.json",
                "content": secret_marker,
            },
        )
    )

    assert result["ok"] is False
    assert "JSON artifact is invalid" in result["reason"]
    assert not (tmp_path / "_topology/business_topology.json").exists()
    audit = (tmp_path / "_audit/opencode_artifact_submissions.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"status":"rejected"' in audit
    assert secret_marker not in audit


def test_artifact_submission_round_trips_through_local_bridge(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    content = "# Bridged analysis\n\n" + ("supported detail\n" * 7_000)

    async def exercise() -> dict[str, object]:
        bridge = FacadeBridge(facade, tmp_path / "artifact.sock")
        await bridge.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(bridge.socket_path))
            request = {
                "token": facade.token,
                "method": "call",
                "name": "submit_artifact",
                "arguments": {
                    "output_artifact": "_analysis/Group.md",
                    "content": content,
                },
            }
            writer.write(json.dumps(request).encode("utf-8") + b"\n")
            await writer.drain()
            response = json.loads((await reader.readline()).decode("utf-8"))
            writer.close()
            await writer.wait_closed()
            return response
        finally:
            await bridge.close()

    response = asyncio.run(exercise())
    assert response["result"]["ok"] is True
    assert (tmp_path / "_analysis/Group.md").read_text() == content


def test_artifact_facade_tool_schema_is_bounded(tmp_path: Path) -> None:
    tool = _facade(tmp_path).list_tools()[0]
    assert tool.name == "submit_artifact"
    assert tool.inputSchema["additionalProperties"] is False
    assert tool.inputSchema["properties"]["content"]["maxLength"] == 200_000


def test_artifact_submission_stdio_child_uses_coordinator_bridge(tmp_path: Path) -> None:
    facade = _facade(tmp_path)

    async def exercise() -> tuple[list[str], dict[str, object]]:
        bridge = FacadeBridge(facade, tmp_path / "artifact-child.sock")
        await bridge.start()
        try:
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mcp_server.providers.opencode_artifact_mcp"],
                env={
                    **os.environ,
                    "NEXUS_ARTIFACT_GRANT": facade.token,
                    "NEXUS_ARTIFACT_SOCKET": str(bridge.socket_path),
                },
            )
            async with (
                stdio_client(parameters) as streams,
                ClientSession(*streams) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool(
                    "submit_artifact",
                    {
                        "output_artifact": "_analysis/Group.md",
                        "content": "# Child-process analysis\n",
                    },
                )
                payload = json.loads(result.content[0].text)
                return [tool.name for tool in tools.tools], payload
        finally:
            await bridge.close()

    tool_names, response = asyncio.run(exercise())
    assert tool_names == ["submit_artifact"]
    assert response["ok"] is True
    assert (tmp_path / "_analysis/Group.md").read_text() == "# Child-process analysis\n"
