from __future__ import annotations

import json
from pathlib import Path

from scripts.agent_runtime_semantic_shadow import runner


async def test_deterministic_semantic_shadow_reports_pass(tmp_path: Path) -> None:
    reports = await runner.run_deterministic(tmp_path)

    assert reports["parity"]["verdict"] == "PASS"
    assert reports["parity"]["tool_count"] == 21
    assert reports["safety"]["verdict"] == "PASS"
    assert reports["safety"]["executor_calls"] == 3
    assert set(reports["safety"]["shadow_effects"].values()) == {0}
    assert reports["selection"]["verdict"] == "NOT_RUN"
    assert reports["selection"]["expected_provider_requests"] == 72

    expected = {
        "slice_2_registry_parity_v1.json",
        "slice_2_shadow_safety_v1.json",
        "slice_2_semantic_selection_v1.json",
    }
    assert {path.name for path in tmp_path.iterdir()} == expected
    for name in expected:
        payload = json.loads((tmp_path / name).read_text())
        assert payload["report_version"] == "1.0.0"


def test_authenticated_subset_contract_is_exact() -> None:
    scenarios = [
        scenario
        for scenario in runner.load_corpus()
        if scenario.family in runner.TARGET_FAMILIES
    ]
    fixture = json.loads(runner.FAMILY_MAP_PATH.read_text())

    assert len(scenarios) == 64
    assert len([item for item in scenarios if item.turns[0].expected.allowed_tool_sequences[0]]) == 25
    assert len([item for item in scenarios if not item.turns[0].expected.allowed_tool_sequences[0]]) == 39
    assert len(fixture["onboarding_prompts"]) == 8
    assert set(fixture["families"]) == runner.TARGET_FAMILIES


def test_semantic_shadow_package_has_no_public_route_import() -> None:
    package = Path(runner.__file__).parent
    source = "\n".join(path.read_text() for path in package.glob("*.py"))

    assert "src.api.routes" not in source
    assert "agent_loop" not in source
    assert "nexus_agent_respond" not in source
