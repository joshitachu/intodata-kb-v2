from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server.experiments.infrastructure.openai_agents.scoped_evidence import (
    ScopedGroundedEvidence,
)
from mcp_server.experiments.application.grounded_runtime import GroundedRoleRuntimeContext
from mcp_server.experiments.infrastructure.openai_agents.grounded import (
    OpenAIAgentsGroundedTaskBuilder,
)
from mcp_server.experiments.mcp_tools.config_compiler import NEXUS_EXPERIMENT_TOOLS


def _packet(root: Path) -> Path:
    (root / "input" / "project_evidence" / "items" / "source-a").mkdir(
        parents=True
    )
    (root / "candidate").mkdir()
    ledger = {
        "call_graph": [{"caller": "ExtractWorkOrders", "callees": ["SendWorkOrders"]}],
        "connections": [{"name": "MaximoApi", "used_by": ["ExtractWorkOrders"]}],
        "job_external_systems": {"SendWorkOrders": ["AssetCheck"]},
        "routines_used_by": {"Formatting": ["SendWorkOrders"]},
        "groups": [{"name": "AssetCheck", "units": ["ExtractWorkOrders", "SendWorkOrders"]}],
    }
    (root / "input" / "FACT_LEDGER.json").write_text(json.dumps(ledger), encoding="utf-8")
    (root / "input" / "project_evidence" / "items" / "source-a" / "scope.bin").write_text(
        "The documented purpose is to send work orders to field inspection.",
        encoding="utf-8",
    )
    (root / "candidate" / "secret-output.md").write_text(
        "must never be readable through evidence tools",
        encoding="utf-8",
    )
    (root / "PACKET.json").write_text("{}", encoding="utf-8")
    return root


def test_scoped_packet_evidence_exposes_only_input_and_derives_scanner_relations(
    tmp_path: Path,
) -> None:
    evidence = ScopedGroundedEvidence.from_packet(_packet(tmp_path / "role-a"), max_context_bytes=50_000)

    listed = evidence.list()
    names = {item["logical_name"] for item in listed["items"]}
    assert names == {
        "input/FACT_LEDGER.json",
        "input/project_evidence/items/source-a/scope.bin",
    }
    assert all("candidate" not in name for name in names)

    searched = evidence.search(query="field inspection")
    assert searched["items"][0]["logical_name"].endswith("scope.bin")
    assert "work orders" in searched["items"][0]["snippet"]

    relation = evidence.traverse(subject_id="unit:ExtractWorkOrders")
    assert {json.dumps(item, sort_keys=True) for item in relation["relationships"]} == {
        json.dumps({
            "evidence_ids": ["input/FACT_LEDGER.json"],
            "predicate": "invokes",
            "source_subject_id": "unit:ExtractWorkOrders",
            "target_subject_id": "unit:SendWorkOrders",
        }, sort_keys=True),
        json.dumps({
            "evidence_ids": ["input/FACT_LEDGER.json"],
            "predicate": "uses_connection",
            "source_subject_id": "unit:ExtractWorkOrders",
            "target_subject_id": "connection:MaximoApi",
        }, sort_keys=True),
        json.dumps({
            "evidence_ids": ["input/FACT_LEDGER.json"],
            "predicate": "belongs_to_group",
            "source_subject_id": "unit:ExtractWorkOrders",
            "target_subject_id": "group:AssetCheck",
        }, sort_keys=True),
    }


def test_scoped_packet_evidence_has_hard_read_bounds_and_no_sibling_access(tmp_path: Path) -> None:
    first = ScopedGroundedEvidence.from_packet(_packet(tmp_path / "roles" / "evidence"), max_context_bytes=50_000)
    _packet(tmp_path / "roles" / "writer")
    (tmp_path / "roles" / "writer" / "input" / "writer-only.txt").write_text(
        "writer only", encoding="utf-8"
    )

    with pytest.raises(PermissionError, match="outside this Grounded role packet"):
        first.read(evidence_id="../writer/input/writer-only.txt", max_bytes=10)
    with pytest.raises(PermissionError, match="outside this Grounded role packet"):
        first.read(evidence_id="candidate/secret-output.md", max_bytes=10)
    with pytest.raises(ValueError, match="read limit"):
        first.read(evidence_id="input/FACT_LEDGER.json", max_bytes=first.max_read_bytes + 1)


def test_scoped_packet_evidence_rejects_symlinked_or_over_budget_input(tmp_path: Path) -> None:
    packet = _packet(tmp_path / "role")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (packet / "input" / "outside.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="symlinks"):
        ScopedGroundedEvidence.from_packet(packet, max_context_bytes=50_000)

    clean = _packet(tmp_path / "other-role")
    with pytest.raises(ValueError, match="exceeds"):
        ScopedGroundedEvidence.from_packet(clean, max_context_bytes=10)


def test_read_deduplicates_a_repeat_evidence_id_but_not_a_different_one(tmp_path: Path) -> None:
    evidence = ScopedGroundedEvidence.from_packet(_packet(tmp_path / "role"), max_context_bytes=50_000)
    evidence_id = "input/project_evidence/items/source-a/scope.bin"

    first = evidence.read(evidence_id=evidence_id, max_bytes=1000)
    assert first["content"] == "The documented purpose is to send work orders to field inspection."
    assert "already_read" not in first

    second = evidence.read(evidence_id=evidence_id, max_bytes=1000)
    assert second["content"] == ""
    assert second["returned_bytes"] == 0
    assert second["already_read"] is True
    assert second["evidence_id"] == first["evidence_id"]
    assert second["sha256"] == first["sha256"]
    assert second["byte_size"] == first["byte_size"]

    other = evidence.read(evidence_id="input/FACT_LEDGER.json", max_bytes=1000)
    assert "already_read" not in other
    assert other["content"]


def test_openai_function_tools_use_the_same_role_local_evidence_surface(tmp_path: Path) -> None:
    class FunctionTool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.name = kwargs["name"]

    packet = _packet(tmp_path / "role")
    builder = OpenAIAgentsGroundedTaskBuilder(
        now=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
        sdk=SimpleNamespace(FunctionTool=FunctionTool),
    )
    request = builder.build(
        GroundedRoleRuntimeContext(
            experiment_id="experiment-1",
            role_id="evidence",
            agent_id="nexus-doc-evidence",
            prompt="Use only Nexus tools.",
            packet_root=packet,
            runtime_directory=tmp_path / "runtime",
            owned_artifacts=("candidate/evidence.json",),
            provider_id="openai",
            model_id="test-model",
            attempt=1,
            max_context_bytes=50_000,
            max_input_tokens=1_000,
            max_tool_calls=10,
            timeout_seconds=60,
        )
    )
    tools = {
        tool.name: tool
        for tool in builder.build_tools(
            request=request,
            allowed_tool_names=NEXUS_EXPERIMENT_TOOLS,
        )
    }

    listed = asyncio.run(
        tools["nexus_experiment_list_evidence"].kwargs["on_invoke_tool"](None, "{}")
    )
    assert {item["logical_name"] for item in listed["items"]} == {
        "input/FACT_LEDGER.json",
        "input/project_evidence/items/source-a/scope.bin",
    }
    traversed = asyncio.run(
        tools["nexus_experiment_traverse_relationships"].kwargs["on_invoke_tool"](
            None, json.dumps({"subject_id": "unit:SendWorkOrders"})
        )
    )
    assert {item["predicate"] for item in traversed["relationships"]} >= {
        "invokes",
        "uses_external_system",
        "uses_routine",
        "belongs_to_group",
    }

    read_args = json.dumps(
        {
            "evidence_id": "input/project_evidence/items/source-a/scope.bin",
            "max_bytes": 1000,
        }
    )
    first_read = asyncio.run(
        tools["nexus_experiment_read_evidence"].kwargs["on_invoke_tool"](None, read_args)
    )
    second_read = asyncio.run(
        tools["nexus_experiment_read_evidence"].kwargs["on_invoke_tool"](None, read_args)
    )
    assert first_read["content"]
    assert second_read["already_read"] is True
    assert second_read["content"] == ""
    assert len(second_read["content"]) < len(first_read["content"])
