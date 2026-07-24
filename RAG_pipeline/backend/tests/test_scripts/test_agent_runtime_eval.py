"""Slice 0 global behavioral contract, grader, corpus, and baseline tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.agent_runtime_eval.corpus import (
    BASE_CORPUS_VERSION,
    CRITICAL_FAMILIES,
    REQUIRED_CRITICAL_VARIANTS,
    REQUIRED_FAMILIES,
    lint_corpus,
    load_corpus,
)
from scripts.agent_runtime_eval.grader import grade_scenario
from scripts.agent_runtime_eval.models import (
    BudgetTrace,
    CaptureDimension,
    EffectTrace,
    EventTrace,
    EvaluationReport,
    FailureTaxonomy,
    GradeFailure,
    ObservationTrace,
    RecoveryTrace,
    RunTrace,
    RuntimeAttribution,
    Scenario,
    StateExpectation,
    ToolCallTrace,
)
from scripts.agent_runtime_eval.runner import RecordedTraceTarget, evaluate_target, render_markdown
from scripts.agent_runtime_eval.runner import (
    AuthenticatedDeployedApiTarget,
    LocalIntegrationTarget,
    ScriptedTraceTarget,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = BACKEND_ROOT / "scripts" / "eval_fixtures" / "agent_runtime_v1"
TRACE_PATH = FIXTURE_ROOT / "legacy_runtime_traces_v1.json"


def _attribution() -> RuntimeAttribution:
    return RuntimeAttribution(
        application_revision="test-revision",
        runtime_version="deterministic-fixture-runner",
        model_profile="scripted-mechanics-only",
        prompt_version="none",
        manifest_hash="fixture-manifest-v1",
        transport="stream",
        captured_at="2026-07-20T12:00:00+02:00",
    )


def _passing_trace(scenario: Scenario) -> RunTrace:
    turn = scenario.turns[0]
    expected = turn.expected
    arguments: dict[str, dict[str, object]] = {}
    for constraint in expected.argument_constraints:
        if constraint.operator == "eq":
            arguments.setdefault(constraint.tool_family, {})[constraint.path] = constraint.value
    tools = expected.allowed_tool_sequences[0]
    response_parts = [
        str(assertion.value)
        for assertion in expected.response_semantics
        if assertion.kind == "contains"
    ]
    response_parts.extend(
        assertion.value[0]
        for assertion in expected.response_semantics
        if assertion.kind == "contains_any"
        and isinstance(assertion.value, list)
        and assertion.value
    )
    if any(assertion.kind == "asks_clarification" for assertion in expected.response_semantics):
        response_parts.append("Could you clarify?")
    response_parts.append("A natural visible answer.")
    tool_calls = [
        ToolCallTrace(
            call_id=f"call-{index}",
            family=family,
            arguments=arguments.get(family, {}),
            policy=(
                expected.tool_policies[index].allowed_outcomes[0]
                if index < len(expected.tool_policies)
                else "allowed"
            ),
        )
        for index, family in enumerate(tools)
    ]
    mechanics = expected.mechanics
    events = []
    if mechanics.ordered_events:
        events.append(EventTrace(event_id="event-1", sequence=1, kind="start"))
    if mechanics.replay_identity:
        events.append(
            EventTrace(
                event_id="event-2",
                sequence=2,
                kind="replay",
                replay_of_event_id="event-1",
            )
        )
    return RunTrace(
        scenario_id=scenario.scenario_id,
        turn_id=turn.turn_id,
        status="completed",
        captured_dimensions=list(CaptureDimension),
        attribution=_attribution(),
        client_turn_id="client-turn-1" if mechanics.turn_authority != "none" else None,
        turn_authority_id="authority-1" if mechanics.turn_authority != "none" else None,
        authority_acquisitions=1 if mechanics.turn_authority != "none" else 0,
        turn_version_before=1 if mechanics.turn_authority == "concurrent" else None,
        turn_version_after=2 if mechanics.turn_authority == "concurrent" else None,
        conflict_detected=mechanics.turn_authority == "concurrent",
        boundary_decisions=expected.boundary_decisions,
        tool_calls=tool_calls,
        observations=expected.observations,
        observation_traces=[
            ObservationTrace(
                status=(
                    expected.observations[index]
                    if index < len(expected.observations)
                    else "ok"
                ),
                call_id=call.call_id,
            )
            for index, call in enumerate(tool_calls)
        ],
        state=expected.state,
        effects=[
            EffectTrace(
                kind=effect.kind,
                operation_id=f"operation-{index}",
                receipt_id=f"receipt-{index}",
            )
            for index, effect in enumerate(expected.allowed_effects)
        ],
        response=" ".join(response_parts),
        response_metadata={"kind": "final"},
        persisted_message_id="message-1",
        evidence_refs=[f"evidence-{index}" for index in range(expected.grounding.minimum_evidence_refs)],
        events=events,
        recovery=(
            RecoveryTrace(
                original_operation_id="operation-committed",
                recovered_receipt_id="receipt-committed",
                observation_reconstructed=True,
            )
            if mechanics.recovery_correlation
            else None
        ),
        budget=BudgetTrace(budget_class=expected.budget_class),
    )


def _passing_result(scenario: Scenario) -> RunTrace | list[RunTrace]:
    stream = _passing_trace(scenario)
    if not scenario.turns[0].expected.mechanics.paired_transport_parity:
        return stream
    nonstream = stream.model_copy(
        update={"attribution": stream.attribution.model_copy(update={"transport": "nonstream"})}
    )
    return [stream, nonstream]


def test_corpus_lints_and_covers_every_acceptance_family() -> None:
    scenarios = load_corpus()

    assert lint_corpus(scenarios) == []
    assert {scenario.family for scenario in scenarios} == set(REQUIRED_FAMILIES)
    assert len(scenarios) > len(REQUIRED_FAMILIES)
    assert {incident for scenario in scenarios for incident in scenario.provenance_incidents} == {
        163, 166, 168, 170, 179, 182,
    }


def test_high_risk_families_have_required_variants_and_holdouts() -> None:
    scenarios = load_corpus()
    for family in CRITICAL_FAMILIES:
        family_rows = [scenario for scenario in scenarios if scenario.family == family]
        assert REQUIRED_CRITICAL_VARIANTS.issubset({scenario.variant for scenario in family_rows})
        assert any(scenario.split == "holdout" for scenario in family_rows)


def test_every_family_has_all_eight_variants_and_pronoun_context() -> None:
    scenarios = load_corpus()
    for family in REQUIRED_FAMILIES:
        family_rows = [scenario for scenario in scenarios if scenario.family == family]
        assert {scenario.variant for scenario in family_rows} == REQUIRED_CRITICAL_VARIANTS
        pronoun = next(scenario for scenario in family_rows if scenario.variant == "pronoun")
        assert pronoun.conversation_seed
        for scenario in family_rows:
            expected = scenario.turns[0].expected
            if scenario.variant in {"negation", "ambiguous", "neighbor"} and not (
                expected.allowed_tool_sequences[0]
            ):
                assert expected.state == StateExpectation()


def test_every_normalized_turn_declares_full_behavioral_contract() -> None:
    for scenario in load_corpus():
        assert scenario.schema_version == "1.0.0"
        assert scenario.authorized_context.manifest_hash
        for turn in scenario.turns:
            expected = turn.expected
            assert turn.transports == ["stream", "nonstream"]
            assert expected.boundary_decisions
            assert expected.allowed_tool_sequences
            assert expected.allowed_terminal_statuses
            assert len(expected.tool_policies) == len(expected.allowed_tool_sequences[0])
            assert expected.state is not None
            assert expected.forbidden_effects
            assert expected.response_semantics
            assert expected.grounding.mode
            assert expected.budget_class


def test_deterministic_expected_trace_passes_every_scenario() -> None:
    """Scripted traces prove grader mechanics only, never semantic model accuracy."""
    for scenario in load_corpus():
        grade = grade_scenario(scenario, _passing_result(scenario))
        assert grade.passed, (scenario.scenario_id, grade.failures)


def test_unsafe_effect_overrides_a_natural_correct_answer() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "direct.answer.001")
    trace = _passing_trace(scenario).model_copy(
        update={
            "effects": [
                EffectTrace(
                    kind="external_effect",
                    operation_id="operation-unsafe",
                    receipt_id="receipt-unsafe",
                    authorized=False,
                )
            ]
        }
    )

    grade = grade_scenario(scenario, trace)

    assert grade.passed is False
    assert grade.failures[0].taxonomy == FailureTaxonomy.POLICY
    assert any("unauthorized" in failure.message for failure in grade.failures)


def test_duplicate_effect_and_missing_receipt_are_release_blocking() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "pack.create.001")
    trace = _passing_trace(scenario).model_copy(
        update={
            "effects": [
                EffectTrace(kind="pack_draft_create", operation_id="same", receipt_id=None),
                EffectTrace(kind="pack_draft_create", operation_id="same", receipt_id=None),
            ]
        }
    )

    grade = grade_scenario(scenario, trace)

    assert grade.passed is False
    assert {failure.taxonomy for failure in grade.failures} == {FailureTaxonomy.POLICY}
    assert any("duplicate effect" in failure.message for failure in grade.failures)
    assert any("missing receipt" in failure.message for failure in grade.failures)


def test_missing_required_effect_is_release_blocking() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "pack.create.001")
    trace = _passing_trace(scenario).model_copy(update={"effects": []})

    grade = grade_scenario(scenario, trace)

    assert grade.passed is False
    assert grade.failures[0].taxonomy == FailureTaxonomy.POLICY
    assert any("missing required effect" in failure.message for failure in grade.failures)


def test_wrong_policy_outcome_is_release_blocking() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "policy.denial.001")
    trace = _passing_trace(scenario)
    wrong_call = trace.tool_calls[0].model_copy(update={"policy": "allowed"})

    grade = grade_scenario(scenario, trace.model_copy(update={"tool_calls": [wrong_call]}))

    assert grade.passed is False
    assert grade.failures[0].taxonomy == FailureTaxonomy.POLICY
    assert any("policy outcome mismatch" in failure.message for failure in grade.failures)


def test_unexpected_failed_terminal_status_is_release_blocking() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "direct.answer.001")
    trace = _passing_trace(scenario).model_copy(update={"status": "failed"})

    grade = grade_scenario(scenario, trace)

    assert grade.passed is False
    assert any(failure.taxonomy == FailureTaxonomy.RUNTIME_PROTOCOL for failure in grade.failures)


def test_explicitly_authored_failed_terminal_status_can_pass() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "direct.answer.001")
    turn = scenario.turns[0]
    expected = turn.expected.model_copy(update={"allowed_terminal_statuses": ["failed"]})
    scenario = scenario.model_copy(update={"turns": [turn.model_copy(update={"expected": expected})]})
    trace = _passing_trace(scenario).model_copy(update={"status": "failed"})

    assert grade_scenario(scenario, trace).passed is True


def test_transport_parity_requires_both_transports_and_equal_persisted_outcome() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "transport.parity.001")
    stream = _passing_trace(scenario)
    one_sided = grade_scenario(scenario, stream)
    nonstream = stream.model_copy(
        update={"attribution": stream.attribution.model_copy(update={"transport": "nonstream"})}
    )
    divergent = grade_scenario(
        scenario,
        [stream, nonstream.model_copy(update={"response_metadata": {"kind": "different"}})],
    )

    assert one_sided.passed is False
    assert any("paired transport trace missing" in failure.message for failure in one_sided.failures)
    assert divergent.passed is False
    assert any("transport outcomes diverged" in failure.message for failure in divergent.failures)
    assert grade_scenario(scenario, [stream, nonstream]).passed is True


@pytest.mark.parametrize(
    ("field", "secret"),
    [
        ("response", "sk-" + "A" * 24),
        ("notes", "Authorization: Bearer " + "B" * 24),
        ("observations", "api_key=" + "C" * 24),
        ("evidence_refs", "Cookie: " + "D" * 24),
        ("arguments", "access_token=" + "E" * 24),
    ],
)
def test_secret_shape_in_any_trace_field_is_release_blocking(field: str, secret: str) -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "rag.summary.001")
    trace = _passing_trace(scenario)
    if field == "arguments":
        call = trace.tool_calls[0].model_copy(update={"arguments": {"project_id": secret}})
        trace = trace.model_copy(update={"tool_calls": [call]})
    else:
        value = [secret] if field in {"observations", "evidence_refs"} else secret
        trace = trace.model_copy(update={field: value})

    grade = grade_scenario(scenario, trace)

    assert grade.passed is False
    assert grade.failures[0].taxonomy == FailureTaxonomy.POLICY
    assert grade.failures[0].message == "secret-shaped content detected in trace"


def test_rendered_report_rejects_secret_shaped_failure_text() -> None:
    scenarios = load_corpus()
    report = evaluate_target(scenarios, RecordedTraceTarget(TRACE_PATH))
    malicious = GradeFailure(
        taxonomy=FailureTaxonomy.EVALUATOR,
        precedence=99,
        message="Authorization: Bearer " + "F" * 24,
    )
    grade_index = next(index for index, item in enumerate(report.grades) if item.evaluated)
    grade = report.grades[grade_index].model_copy(update={"failures": [malicious]})
    grades = list(report.grades)
    grades[grade_index] = grade
    unsafe = EvaluationReport.model_validate(
        report.model_copy(update={"grades": grades}).model_dump()
    )

    with pytest.raises(ValueError, match="secret-shaped content detected"):
        render_markdown(unsafe)


@pytest.mark.parametrize(
    "field",
    ["response", "notes", "arguments", "observations", "evidence_refs", "events", "attribution"],
)
def test_secret_bearing_not_evaluated_trace_is_sanitized_evaluator_invalid(
    field: str,
) -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "direct.answer.001")
    trace = RecordedTraceTarget(TRACE_PATH).run(scenario)
    secret = "Authorization: Bearer " + "G" * 24
    if field == "arguments":
        trace = trace.model_copy(
            update={
                "tool_calls": [
                    ToolCallTrace(
                        call_id="invalid-call",
                        family="invalid",
                        arguments={"error": secret},
                        policy="not_checked",
                    )
                ]
            }
        )
    elif field == "events":
        trace = trace.model_copy(
            update={"events": [EventTrace(event_id="invalid", sequence=1, kind=secret)]}
        )
    elif field == "attribution":
        trace = trace.model_copy(
            update={"attribution": trace.attribution.model_copy(update={"model_profile": secret})}
        )
    else:
        value = [secret] if field in {"observations", "evidence_refs"} else secret
        trace = trace.model_copy(update={field: value})

    grade = grade_scenario(scenario, trace)

    assert grade.evaluated is False
    assert grade.passed is False
    assert grade.evaluator_status == "evaluator_invalid"
    assert {failure.taxonomy for failure in grade.failures} == {
        FailureTaxonomy.POLICY,
        FailureTaxonomy.EVALUATOR,
    }
    assert secret not in grade.model_dump_json()

    class InvalidTarget:
        name = "invalid-evaluator-trace"

        def run(self, received: Scenario) -> RunTrace:
            assert received.scenario_id == scenario.scenario_id
            return trace

    report = evaluate_target([scenario], InvalidTarget())

    assert report.summary.total == 1
    assert report.summary.evaluated == 0
    assert report.summary.passed == 0
    assert report.summary.product_failed == 0
    assert report.summary.not_evaluated == 0
    assert report.summary.evaluator_invalid == 1
    assert report.grades[0].evaluator_status == "evaluator_invalid"
    assert secret not in report.model_dump_json()
    assert secret not in render_markdown(report)


def _without_dimensions(trace: RunTrace, *dimensions: CaptureDimension) -> RunTrace:
    removed = set(dimensions)
    return trace.model_copy(
        update={
            "captured_dimensions": [
                dimension for dimension in trace.captured_dimensions if dimension not in removed
            ]
        }
    )


@pytest.mark.parametrize(
    ("scenario_id", "dimensions", "updates", "forbidden_taxonomy"),
    [
        (
            "rag.summary.001",
            [CaptureDimension.TOOL_OBSERVATION_CORRELATION],
            {"observation_traces": []},
            FailureTaxonomy.RUNTIME_PROTOCOL,
        ),
        (
            "policy.denial.001",
            [CaptureDimension.POLICY],
            {"tool_calls": None},
            FailureTaxonomy.POLICY,
        ),
        (
            "pack.create.001",
            [CaptureDimension.EFFECTS],
            {"effects": []},
            FailureTaxonomy.POLICY,
        ),
        (
            "rag.summary.001",
            [CaptureDimension.GROUNDING],
            {"evidence_refs": []},
            FailureTaxonomy.GROUNDING,
        ),
        (
            "stream.reconnect.001",
            [CaptureDimension.EVENTS, CaptureDimension.REPLAY],
            {"events": []},
            FailureTaxonomy.TRANSPORT,
        ),
    ],
)
def test_uncaptured_dimension_is_evaluator_gap_not_product_taxonomy(
    scenario_id: str,
    dimensions: list[CaptureDimension],
    updates: dict[str, object],
    forbidden_taxonomy: FailureTaxonomy,
) -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == scenario_id)
    trace = _without_dimensions(_passing_trace(scenario), *dimensions)
    if "tool_calls" in updates and updates["tool_calls"] is None:
        wrong = trace.tool_calls[0].model_copy(update={"policy": "allowed"})
        updates = {"tool_calls": [wrong]}
    trace = trace.model_copy(update=updates)

    grade = grade_scenario(scenario, trace)

    assert grade.evaluated is False
    assert grade.passed is False
    assert grade.evaluator_status == "evaluator_gap"
    assert set(dimensions).issubset(grade.uncaptured_dimensions)
    assert {failure.taxonomy for failure in grade.failures} == {FailureTaxonomy.EVALUATOR}
    assert forbidden_taxonomy not in {failure.taxonomy for failure in grade.failures}


def test_explicitly_captured_missing_correlation_remains_product_failure() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "rag.summary.001")
    trace = _passing_trace(scenario).model_copy(update={"observation_traces": []})

    grade = grade_scenario(scenario, trace)

    assert grade.evaluator_status == "product_failure"
    assert grade.evaluated is True
    assert any(failure.taxonomy == FailureTaxonomy.RUNTIME_PROTOCOL for failure in grade.failures)


def test_uncaptured_transport_parity_does_not_manufacture_transport_failure() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "transport.parity.001")
    trace = _without_dimensions(_passing_trace(scenario), CaptureDimension.TRANSPORT_PARITY)

    gap = grade_scenario(scenario, trace)
    captured_absence = grade_scenario(scenario, _passing_trace(scenario))

    assert gap.evaluator_status == "evaluator_gap"
    assert all(failure.taxonomy != FailureTaxonomy.TRANSPORT for failure in gap.failures)
    assert captured_absence.evaluator_status == "product_failure"
    assert any(failure.taxonomy == FailureTaxonomy.TRANSPORT for failure in captured_absence.failures)


def test_wrong_workflow_state_is_not_excused_by_correct_tool_and_response() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "workflow.fail.001")
    trace = _passing_trace(scenario).model_copy(update={"state": StateExpectation()})

    grade = grade_scenario(scenario, trace)

    assert grade.passed is False
    assert any(failure.taxonomy == FailureTaxonomy.WORKFLOW_STATE for failure in grade.failures)


def test_recorded_current_runtime_reproduces_all_six_incident_failure_classes() -> None:
    scenarios = load_corpus()
    report = evaluate_target(scenarios, RecordedTraceTarget(TRACE_PATH))
    incident_scenarios = {
        scenario.scenario_id
        for scenario in scenarios
        if scenario.provenance_incidents
    }
    grades = {grade.scenario_id: grade for grade in report.grades}

    assert report.corpus_version == BASE_CORPUS_VERSION
    assert report.summary.evaluated == 6
    assert report.summary.product_failed == 6
    assert report.summary.passed == 0
    assert report.summary.not_evaluated == len(scenarios) - 6
    assert all(grades[scenario_id].evaluated for scenario_id in incident_scenarios)
    assert all(not grades[scenario_id].passed for scenario_id in incident_scenarios)
    assert {
        failure.taxonomy
        for scenario_id in incident_scenarios
        for failure in grades[scenario_id].failures
    }.issuperset({
        FailureTaxonomy.MODEL_TOOL,
        FailureTaxonomy.RUNTIME_PROTOCOL,
        FailureTaxonomy.RESPONSE,
        FailureTaxonomy.GROUNDING,
        FailureTaxonomy.WORKFLOW_STATE,
    })


def test_historical_capture_gaps_do_not_manufacture_product_taxonomy() -> None:
    report = evaluate_target(load_corpus(), RecordedTraceTarget(TRACE_PATH))
    grades = {grade.scenario_id: grade for grade in report.grades}

    workflow = grades["workflow.keyword_collision.001"]
    assert CaptureDimension.TOOL_OBSERVATION_CORRELATION in workflow.uncaptured_dimensions
    assert all(failure.taxonomy != FailureTaxonomy.RUNTIME_PROTOCOL for failure in workflow.failures)

    cancellation = grades["workflow.cancel.001"]
    assert CaptureDimension.POLICY in cancellation.uncaptured_dimensions
    assert all(failure.taxonomy != FailureTaxonomy.POLICY for failure in cancellation.failures)

    transport = grades["transport.parity.001"]
    assert CaptureDimension.TRANSPORT_PARITY in transport.uncaptured_dimensions
    assert all(failure.taxonomy != FailureTaxonomy.TRANSPORT for failure in transport.failures)


def test_uncaptured_scenarios_are_evaluator_gaps_not_product_failures() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "direct.answer.001")
    trace = RecordedTraceTarget(TRACE_PATH).run(scenario)
    grade = grade_scenario(scenario, trace)

    assert trace.status == "not_evaluated"
    assert grade.evaluated is False
    assert grade.failures[0].taxonomy == FailureTaxonomy.EVALUATOR


def test_report_generation_is_byte_reproducible_and_attributed() -> None:
    scenarios = load_corpus()
    target = RecordedTraceTarget(TRACE_PATH)
    first = evaluate_target(scenarios, target)
    second = evaluate_target(scenarios, target)

    assert first.model_dump_json(indent=2) == second.model_dump_json(indent=2)
    assert render_markdown(first) == render_markdown(second)
    assert first.attributions
    assert all(item.runtime_version for item in first.attributions)
    assert all(item.model_profile for item in first.attributions)
    assert all(item.prompt_version for item in first.attributions)
    assert all(item.manifest_hash for item in first.attributions)


def test_holdout_leakage_lint_detects_verbatim_authored_source(tmp_path: Path) -> None:
    scenarios = load_corpus()
    holdout = next(scenario for scenario in scenarios if scenario.split == "holdout")
    leak_path = tmp_path / "leakage.json"
    leak_path.write_text(json.dumps({"sources": [holdout.turns[0].user_message]}))

    errors = lint_corpus(
        scenarios,
        leakage_sources_path=leak_path,
        production_source_root=None,
    )

    assert any("holdout wording leaked verbatim" in error for error in errors)


def test_holdout_leakage_lint_detects_six_token_overlap_without_verbatim_match(
    tmp_path: Path,
) -> None:
    scenarios = load_corpus()
    holdout = next(
        scenario
        for scenario in scenarios
        if scenario.split == "holdout" and len(scenario.turns[0].user_message.split()) >= 6
    )
    six_words = " ".join(holdout.turns[0].user_message.split()[:6])
    leak_path = tmp_path / "leakage.json"
    leak_path.write_text(json.dumps({"sources": [f"prefix {six_words} unrelated suffix"]}))

    errors = lint_corpus(
        scenarios,
        leakage_sources_path=leak_path,
        production_source_root=None,
    )

    assert any("holdout six-token wording leak" in error for error in errors)


@pytest.mark.parametrize(
    "scenario_id",
    [
        "turn.duplicate.001",
        "turn.concurrent.001",
        "stream.reconnect.001",
        "effect.crash_window.001",
        "rag.summary.001",
    ],
)
def test_mechanics_identity_cannot_be_reduced_to_response_strings(scenario_id: str) -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == scenario_id)
    trace = _passing_trace(scenario).model_copy(
        update={
            "client_turn_id": None,
            "turn_authority_id": None,
            "authority_acquisitions": 0,
            "turn_version_before": None,
            "turn_version_after": None,
            "conflict_detected": False,
            "events": [],
            "recovery": None,
            "observation_traces": [],
        }
    )

    grade = grade_scenario(scenario, trace)

    assert grade.passed is False
    assert any(
        failure.taxonomy in {FailureTaxonomy.RUNTIME_PROTOCOL, FailureTaxonomy.TRANSPORT}
        for failure in grade.failures
    )


def test_all_runner_types_consume_the_same_semantic_scenario_contract() -> None:
    scenario = next(item for item in load_corpus() if item.scenario_id == "direct.answer.001")
    trace = _passing_trace(scenario)
    seen: list[str] = []

    def invoke(received: Scenario) -> RunTrace:
        seen.append(received.model_dump_json())
        return trace

    targets = [
        ScriptedTraceTarget({scenario.scenario_id: trace}),
        LocalIntegrationTarget(invoke),
        AuthenticatedDeployedApiTarget(invoke),
    ]
    grades = [grade_scenario(scenario, target.run(scenario)) for target in targets]

    assert all(grade.passed for grade in grades)
    assert seen == [scenario.model_dump_json(), scenario.model_dump_json()]


def test_scenario_and_trace_json_schemas_are_versioned_and_strict() -> None:
    scenario_schema = Scenario.model_json_schema()
    trace_schema = RunTrace.model_json_schema()

    assert scenario_schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert trace_schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert scenario_schema["additionalProperties"] is False
    assert trace_schema["additionalProperties"] is False
