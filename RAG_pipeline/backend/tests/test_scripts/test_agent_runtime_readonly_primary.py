from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.agent_runtime_readonly_primary import runner


@pytest.mark.asyncio
async def test_deterministic_reports_pass(tmp_path: Path) -> None:
    reports = await runner.run_deterministic(tmp_path)

    assert reports["grounding"]["verdict"] == "PASS"
    assert reports["parity"]["verdict"] == "PASS"
    assert reports["parity"]["stream_equals_nonstream"] is True
    assert reports["parity"]["unvalidated_content_persisted"] is False
    assert reports["selection"]["verdict"] == "NOT_RUN"

    # All six no-evidence states map and mislabels are rejected.
    states = {item["state"] for item in reports["grounding"]["no_evidence_state_mappings"]}
    assert {
        "no_evidence",
        "source_unavailable",
        "scope_denied",
        "tool_failed",
        "partial",
        "conflicting",
    } <= states
    assert reports["grounding"]["leak_guard_blocks_internal_output"] is True
    # A real read executed; no consequential handler ran.
    effects = reports["grounding"]["effects"]
    assert effects["consequential_executions"] == 0
    assert effects["writes"] == 0
    assert effects["executor_read_calls"] >= 1

    expected = {
        "slice_3_readonly_selection_v1.json",
        "slice_3_grounding_safety_v1.json",
        "slice_3_transport_parity_v1.json",
    }
    assert {p.name for p in tmp_path.iterdir()} == expected


def test_subset_contract_is_exact() -> None:
    subset = runner.load_subset()
    assert len(subset["no_action_families"]) == 10
    assert len(subset["read_only_families"]) == 8
    assert len(subset["no_evidence_families"]) == 6
    assert subset["tracked_miss_scenario_id"] == "rag.summary.001.keyword_collision"
    assert subset["thresholds"]["boundary_rate_minimum"] == 0.95


def test_reports_are_sanitized(tmp_path: Path) -> None:
    import asyncio

    reports = asyncio.run(runner.run_deterministic(tmp_path))
    for name in tmp_path.iterdir():
        payload = json.loads(name.read_text())
        # require_sanitized already ran on write; re-affirm no secret-like content.
        runner.require_sanitized(payload, label=name.name)
    assert reports["grounding"]["verdict"] == "PASS"


def test_package_imports_no_public_route() -> None:
    package = Path(runner.__file__).parent
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    assert "src.api.routes" not in source
    assert "execute_production_agent_chat_turn" not in source
