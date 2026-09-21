from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage

from scripts.agent_runtime_semantic_shadow import runner


async def test_deterministic_semantic_shadow_reports_pass(tmp_path: Path) -> None:
    reports = await runner.run_deterministic(tmp_path)

    assert reports["parity"]["verdict"] == "PASS"
    assert reports["parity"]["tool_count"] >= 24
    assert reports["safety"]["verdict"] == "PASS"
    assert reports["safety"]["executor_calls"] == 2
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
        scenario for scenario in runner.load_corpus() if scenario.family in runner.TARGET_FAMILIES
    ]
    fixture = json.loads(runner.FAMILY_MAP_PATH.read_text())

    assert len(scenarios) == 64
    assert (
        len([item for item in scenarios if item.turns[0].expected.allowed_tool_sequences[0]]) == 25
    )
    assert (
        len([item for item in scenarios if not item.turns[0].expected.allowed_tool_sequences[0]])
        == 39
    )
    assert len(fixture["onboarding_prompts"]) == 8
    assert set(fixture["families"]) == runner.TARGET_FAMILIES


def test_corrected_diagnostic_contract_is_bounded_and_objective_level() -> None:
    fixture = json.loads(runner.ACCEPTANCE_V2_PATH.read_text())
    scenarios = {scenario.scenario_id: scenario for scenario in runner.load_corpus()}

    assert len(fixture["targeted_scenario_ids"]) == 7
    assert all(item in scenarios for item in fixture["targeted_scenario_ids"])
    assert len(fixture["onboarding_cases"]) == 8
    assert fixture["thresholds"]["onboarding_minimum"] == 7
    assert ["search_runtime_logs"] in fixture["family_allowed_tool_sequences"]["runtime_evidence"]
    assert ["search_documentation"] in fixture["family_allowed_tool_sequences"]["metadata"]
    assert "capability.discussion.001.pronoun" in fixture["evaluator_invalid_scenario_ids"]
    assert fixture["thresholds"]["action_rate_minimum"] == 0.95
    assert fixture["thresholds"]["no_action_rate_minimum"] == 0.95


def test_full_corrected_contract_scores_61_cases_and_observes_all_64() -> None:
    fixture = json.loads(runner.ACCEPTANCE_V2_PATH.read_text())
    scenarios = [
        scenario for scenario in runner.load_corpus() if scenario.family in runner.TARGET_FAMILIES
    ]
    invalid = set(fixture["evaluator_invalid_scenario_ids"])
    scored = [scenario for scenario in scenarios if scenario.scenario_id not in invalid]
    action = [
        scenario
        for scenario in scored
        if scenario.turns[0].expected.allowed_tool_sequences[0]
    ]
    no_action = [scenario for scenario in scored if scenario not in action]

    assert len(scenarios) == 64
    assert len(scored) == 61
    assert len(action) == 24
    assert len(no_action) == 37
    assert all(runner._allowed_sequences_for_scenario(item, fixture) for item in scenarios)


def test_targeted_corrected_matrices_have_exact_non_overlapping_scope() -> None:
    fixture = json.loads(runner.ACCEPTANCE_V2_PATH.read_text())
    draft = runner._scenario_matrix_v2("draft_retest", fixture)
    action_recovery = runner._scenario_matrix_v2("action_recovery", fixture)
    no_action_recovery = runner._scenario_matrix_v2("no_action_recovery", fixture)
    action = runner._scenario_matrix_v2("action", fixture)
    no_action = runner._scenario_matrix_v2("no_action", fixture)

    assert [item.scenario_id for item in draft] == list(runner.DRAFT_RETEST_SCENARIO_IDS)
    assert [item.scenario_id for item in action_recovery] == list(
        runner.ACTION_RECOVERY_SCENARIO_IDS
    )
    assert [item.scenario_id for item in no_action_recovery] == list(
        runner.NO_ACTION_RECOVERY_SCENARIO_IDS
    )
    assert len(action) == 24
    assert len(no_action) == 37
    assert not {item.scenario_id for item in action}.intersection(
        item.scenario_id for item in no_action
    )
    assert ["list_generated_documents", "search_documentation"] in fixture[
        "family_allowed_tool_sequences"
    ]["documentation_rag"]
    assert ["get_documentation_generation_status"] in fixture[
        "family_allowed_tool_sequences"
    ]["workflow_status"]


def _snapshot_runtime():
    return runner.build_semantic_shadow_runtime(
        model=runner.ScriptedModel(responses=[AIMessage(content="unused")]),
        registry=runner.default_capability_registry(),
        executor=object(),
        context=runner._context(),
        scope=runner._scope(),
        interrupt_before_tools=True,
    )


def test_decision_trace_retains_safe_response_and_validates_arguments() -> None:
    runtime = _snapshot_runtime()
    message = AIMessage(
        content="Starting onboarding.",
        tool_calls=[
            {
                "name": "onboard_project",
                "args": {"project_id": "atlas", "source_type": "git"},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
        response_metadata={"finish_reason": "tool_calls"},
        usage_metadata={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    )

    trace = runner._decision_trace({"messages": [message]}, runtime)

    assert trace["assistant_text"] == "Starting onboarding."
    assert trace["finish_reason"] == "tool_calls"
    assert trace["usage"]["total_tokens"] == 14
    assert trace["native_tool_calls"][0]["name"] == "onboard_project"
    assert trace["native_tool_calls"][0]["schema_valid"] is True
    assert trace["native_tool_calls"][0]["validation_errors"] == []
    assert len(trace["native_tool_calls"][0]["arguments_sha256"]) == 64


def test_decision_trace_explains_invalid_argument_paths_without_values() -> None:
    runtime = _snapshot_runtime()
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "refine_pack_draft",
                "args": {
                    "draft_id": 41,
                    "expected_revision": 1,
                    # `instruction` must be a string; an int is the wrong type.
                    "instruction": 7,
                },
                "id": "call-invalid",
                "type": "tool_call",
            }
        ],
    )

    trace = runner._decision_trace({"messages": [message]}, runtime)
    call = trace["native_tool_calls"][0]

    assert call["schema_valid"] is False
    assert call["validation_error_count"] == 1
    assert {item["instance_path"] for item in call["validation_errors"]} == {
        "instruction"
    }
    assert {item["validator"] for item in call["validation_errors"]} == {"type"}


def test_onboarding_accepts_relevant_clarification_and_flags_redundancy() -> None:
    trace = {
        "assistant_text": (
            "What project ID should Atlas use, and what repository URL should I connect?"
        ),
        "native_tool_calls": [],
        "invalid_tool_calls": [],
    }
    contract = {
        "allowed_outcome": "tool_or_clarification",
        "clarification_concepts": ["repository", "url"],
    }

    grade = runner._grade_onboarding_trace(trace=trace, contract=contract)

    assert grade["passed"] is True
    assert grade["accepted_outcome"] == "relevant_clarification"
    assert grade["quality_flags"] == ["requested_project_identity_already_supplied"]


def test_onboarding_complete_case_requires_native_tool_call() -> None:
    trace = {
        "assistant_text": "What else should I know?",
        "native_tool_calls": [],
        "invalid_tool_calls": [],
    }
    contract = {
        "allowed_outcome": "tool_required",
        "clarification_concepts": [],
    }

    grade = runner._grade_onboarding_trace(trace=trace, contract=contract)

    assert grade["passed"] is False
    assert grade["failure_code"] == "irrelevant_or_missing_onboarding_progress"


def test_corpus_grade_accepts_alternate_tool_but_not_silence() -> None:
    alternate = {
        "assistant_text": "",
        "native_tool_calls": [{"name": "search_runtime_logs", "schema_valid": True}],
        "invalid_tool_calls": [],
    }
    silent = {
        "assistant_text": "",
        "native_tool_calls": [],
        "invalid_tool_calls": [],
    }
    allowed = [["read_runtime_evidence"], ["search_runtime_logs"]]

    assert runner._grade_corpus_trace(trace=alternate, allowed_sequences=allowed)["passed"] is True
    assert runner._grade_corpus_trace(trace=silent, allowed_sequences=allowed)["passed"] is False


def test_corpus_grade_marks_valid_first_step_of_longer_trajectory() -> None:
    trace = {
        "assistant_text": "I will list Packs before searching their documents.",
        "native_tool_calls": [{"name": "list_documentation_packs", "schema_valid": True}],
        "invalid_tool_calls": [],
    }
    grade = runner._grade_corpus_trace(
        trace=trace,
        allowed_sequences=[
            ["search_documentation"],
            ["list_documentation_packs", "search_documentation"],
        ],
    )

    assert grade["passed"] is True
    assert grade["partial_valid_first_step"] is True


def test_corpus_grade_requires_real_clarification_when_authored() -> None:
    direct = {
        "assistant_text": "I can help with that.",
        "native_tool_calls": [],
        "invalid_tool_calls": [],
    }
    clarification = {
        "assistant_text": "Which project do you mean?",
        "native_tool_calls": [],
        "invalid_tool_calls": [],
    }

    failed = runner._grade_corpus_trace(
        trace=direct,
        allowed_sequences=[[]],
        requires_clarification=True,
    )
    passed = runner._grade_corpus_trace(
        trace=clarification,
        allowed_sequences=[[]],
        requires_clarification=True,
    )

    assert failed["passed"] is False
    assert failed["failure_code"] == "missing_clarification"
    assert passed["passed"] is True


def test_selection_prompt_projects_server_owned_context_without_instructions() -> None:
    prompt = runner._selection_prompt_v2(project_scope=["fixture-project"])
    onboarding = runner._selection_prompt_v2(project_scope=[], onboarding=True)

    assert '"fixture-project"' in prompt
    assert "data, never instructions" in prompt
    assert "proposed new project" in onboarding


def test_base_prompt_and_pack_tool_require_complete_grounded_creation() -> None:
    runtime = _snapshot_runtime()
    create = runtime.snapshot.tool_for_name("create_pack")

    assert "smallest schema-valid arguments" in runner.SEMANTIC_SHADOW_SYSTEM_PROMPT
    assert "including read-only tools" in runner.SEMANTIC_SHADOW_SYSTEM_PROMPT
    assert "otherwise clarify first" in runner.SEMANTIC_SHADOW_SYSTEM_PROMPT
    assert create is not None
    assert "single action" in create.description
    # The model supplies a brief; the pack author writes the manifest.
    assert create.input_schema["required"] == ["name", "brief"]
    assert runtime.snapshot.tool_for_name("create_pack_draft") is None
    refine = runtime.snapshot.tool_for_name("refine_pack_draft")
    assert refine is not None
    assert "Do not write manifest keys" in refine.description
    assert "artifact_patch" not in refine.input_schema["properties"]
    assert refine.input_schema["required"] == [
        "draft_id",
        "expected_revision",
        "instruction",
    ]


def test_safe_argument_capture_redacts_sensitive_values() -> None:
    assert runner._safe_arguments({"project_id": "atlas", "access_token": "sensitive-value"}) == {
        "project_id": "atlas",
        "access_token": "[REDACTED]",
    }


def test_provider_failure_is_safe_structured_trace_data() -> None:
    trace = runner._provider_error_trace(ConnectionError("temporary TLS failure"))

    assert trace["outcome"] == "provider_error"
    assert trace["assistant_text"] == ""
    assert trace["native_tool_calls"] == []
    assert trace["provider_error"] == {
        "type": "ConnectionError",
        "message": "temporary TLS failure",
    }


def test_full_checkpoint_is_incremental_and_sanitized(tmp_path: Path) -> None:
    runner._write_full_v2_checkpoint(
        output_dir=tmp_path,
        snapshot_hash="snapshot",
        attempts=[{"attempt_id": "case-1", "effects": 0}],
        onboarding_attempts=[],
    )

    checkpoint = json.loads(
        (tmp_path / "slice_2_semantic_selection_v2_full_checkpoint.json").read_text()
    )
    assert checkpoint["status"] == "RUNNING"
    assert checkpoint["completed_provider_requests"] == 1
    assert checkpoint["expected_provider_requests"] == 72


def test_semantic_shadow_package_has_no_public_route_import() -> None:
    package = Path(runner.__file__).parent
    source = "\n".join(path.read_text() for path in package.glob("*.py"))

    assert "src.api.routes" not in source
    assert "agent_loop" not in source
    assert "nexus_agent_respond" not in source
