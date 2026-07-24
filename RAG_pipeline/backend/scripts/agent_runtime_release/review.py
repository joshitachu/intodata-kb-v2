"""Deterministic public-event review for authenticated Slice 7 captures."""

from __future__ import annotations

import re
from collections import defaultdict

from scripts.agent_runtime_eval.models import Scenario, SemanticAssertion

from .capture import PublicApiCapture
from .models import AttemptEvidence, CaptureReview


TOOL_LABEL_TO_FAMILY = {
    "cancel workflow": "workflow_cancel",
    "create pack draft": "pack_draft_create",
    "list active project workflows": "workflow_status_read",
    "list documentation packs": "pack_list",
    "list generated project documentation": "project_document_list",
    "list project documents": "project_document_list",
    "query project metadata": "project_metadata_search",
    "read document": "document_read",
    "read relevant passages from an indexed document": "document_read",
    "read conversation summary": "conversation_summary_read",
    "read pack draft": "pack_draft_read",
    "read runtime evidence": "runtime_failure_read",
    "save pack": "pack_save",
    "search documentation": "documentation_search",
    "search runtime logs": "runtime_log_search",
}


def _payload(event: dict) -> dict:
    value = event.get("public_payload", event.get("payload", {}))
    return value if isinstance(value, dict) else {}


def _actual_tools(capture: PublicApiCapture) -> list[str]:
    tools: list[str] = []
    for event in capture.public_events:
        if event.get("type") != "tool.proposed":
            continue
        label = str(_payload(event).get("tool_label", "")).strip()
        if not label or label.casefold() == "tool":
            continue
        family = TOOL_LABEL_TO_FAMILY.get(label.casefold(), f"unmapped:{label}")
        tools.append(family)
    return tools


def _evidence_ref_count(capture: PublicApiCapture) -> int:
    refs: set[str] = set()
    anonymous = 0
    for event in capture.public_events:
        if event.get("type") != "tool.completed":
            continue
        for ref in _payload(event).get("evidence_refs", []) or []:
            if isinstance(ref, dict):
                identifier = str(ref.get("evidence_id", "")).strip()
            else:
                identifier = str(ref).strip()
            if identifier:
                refs.add(identifier)
            else:
                anonymous += 1
    return len(refs) + anonymous


def _asks_clarification(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    markers = (
        "clarif", "need a bit more context", "need more context",
        "not sure what", "what does that refer", "what specific",
        "please provide", "could you provide", "could you share", "could you tell me",
    )
    return "?" in text and any(marker in normalized for marker in markers)


def _response_failure(assertion: SemanticAssertion, text: str) -> str | None:
    normalized = text.casefold()
    if assertion.kind == "nonempty" and not text.strip():
        return "assistant response is empty"
    if assertion.kind == "contains" and str(assertion.value).casefold() not in normalized:
        return f"response does not contain {assertion.value!r}"
    if assertion.kind == "contains_any":
        choices = assertion.value if isinstance(assertion.value, list) else []
        if not any(choice.casefold() in normalized for choice in choices):
            return f"response does not contain any of {choices!r}"
    if assertion.kind == "excludes" and str(assertion.value).casefold() in normalized:
        return f"response exposes excluded text {assertion.value!r}"
    if assertion.kind == "asks_clarification" and not _asks_clarification(text):
        return "response does not ask a bounded clarification"
    if assertion.kind == "word_count_at_most" and len(text.split()) > int(assertion.value or 0):
        return f"response exceeds {assertion.value} words"
    if assertion.kind == "list_items_at_least":
        list_items = len(re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+", text))
        if list_items < int(assertion.value or 0):
            return f"response has {list_items} list items; expected {assertion.value}"
    return None


def _append_failure(
    attempt: AttemptEvidence,
    *,
    failure: str,
    taxonomy: str,
    rationale: str,
) -> None:
    if failure not in attempt.deterministic_failures:
        attempt.deterministic_failures.append(failure)
    if taxonomy not in attempt.taxonomy:
        attempt.taxonomy.append(taxonomy)
    attempt.passed = False
    attempt.review_rationale = f"{attempt.review_rationale} {rationale}".strip()


def _review_one(capture: PublicApiCapture, scenario: Scenario) -> AttemptEvidence:
    expected = scenario.turns[0].expected
    tools = _actual_tools(capture)
    clarified = _asks_clarification(capture.assistant_text)
    attempt = AttemptEvidence(
        attempt_id=capture.attempt_id,
        scenario_id=capture.scenario_id,
        prompt_id=capture.prompt_id,
        family=capture.family,
        authenticated=True,
        target="authenticated-public-api",
        passed=True,
        assistant_text=capture.assistant_text,
        review_rationale=(
            f"Expected tools {expected.allowed_tool_sequences}; observed {tools or []}."
        ),
        tool_trajectory=tools,
        safe_arguments=[],
        latency_ms=capture.latency_ms,
        input_tokens=capture.input_tokens,
        output_tokens=capture.output_tokens,
        cost_usd=capture.cost_usd,
        clarification_expected=any(
            item.kind == "asks_clarification" for item in expected.response_semantics
        ),
        clarified=clarified,
    )

    event_types = [str(event.get("type", "")) for event in capture.public_events]
    terminal = "completed" if "run.completed" in event_types else (
        "failed" if "run.failed" in event_types else "missing"
    )
    if not capture.public_events:
        _append_failure(
            attempt, failure="checkpoint_serialization", taxonomy="EVALUATOR",
            rationale="Public event evidence is missing.",
        )
    if capture.status_code != 200 or terminal not in expected.allowed_terminal_statuses:
        _append_failure(
            attempt, failure="runtime_protocol", taxonomy="RUNTIME_PROTOCOL",
            rationale=(
                f"Terminal status {terminal!r} (HTTP {capture.status_code}) is outside "
                f"{expected.allowed_terminal_statuses}."
            ),
        )
    if tools not in expected.allowed_tool_sequences:
        _append_failure(
            attempt, failure="trajectory", taxonomy="MODEL_TOOL",
            rationale=f"Tool path {tools or []} does not match the authored path.",
        )

    response_failures = [
        failure
        for assertion in expected.response_semantics
        if (failure := _response_failure(assertion, capture.assistant_text)) is not None
    ]
    if response_failures:
        _append_failure(
            attempt, failure="response", taxonomy="RESPONSE",
            rationale="; ".join(response_failures) + ".",
        )

    evidence_refs = _evidence_ref_count(capture)
    if (
        expected.grounding.mode in {"required", "partial", "conflict"}
        and evidence_refs < expected.grounding.minimum_evidence_refs
    ):
        _append_failure(
            attempt, failure="grounding", taxonomy="GROUNDING",
            rationale=(
                f"Only {evidence_refs} public evidence reference(s) were retained; "
                f"{expected.grounding.minimum_evidence_refs} required."
            ),
        )

    mechanics = expected.mechanics
    sequences = [event.get("sequence") for event in capture.public_events if event.get("sequence") is not None]
    if mechanics.ordered_events and (sequences != sorted(sequences) or len(sequences) != len(set(sequences))):
        _append_failure(
            attempt, failure="event_order", taxonomy="TRANSPORT",
            rationale="Public event sequence is not strictly ordered.",
        )
    replayed = any(event.get("replayed") is True for event in capture.public_events)
    if mechanics.replay_identity and not replayed:
        _append_failure(
            attempt, failure="restart_replay", taxonomy="TRANSPORT",
            rationale="The capture contains no replayed terminal identity.",
        )
    if mechanics.turn_authority == "deduplicated" and not replayed:
        _append_failure(
            attempt, failure="turn_authority_evidence", taxonomy="RUNTIME_PROTOCOL",
            rationale="The capture did not exercise a duplicate replay, so deduplication is unproven.",
        )
    if attempt.passed:
        attempt.review_rationale += " Deterministic terminal, path, response, grounding, and mechanics checks passed."
    return attempt


def review_captures(
    captures: list[PublicApiCapture],
    scenarios: list[Scenario],
) -> CaptureReview:
    scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    attempts: list[AttemptEvidence] = []
    capture_by_attempt_id = {capture.attempt_id: capture for capture in captures}
    for capture in captures:
        scenario = scenario_by_id.get(capture.scenario_id)
        if scenario is None:
            raise ValueError(f"capture references unknown scenario {capture.scenario_id!r}")
        attempts.append(_review_one(capture, scenario))

    parity_groups: dict[str, list[AttemptEvidence]] = defaultdict(list)
    for attempt in attempts:
        scenario = scenario_by_id[attempt.scenario_id]
        if scenario.turns[0].expected.mechanics.paired_transport_parity:
            parity_groups[attempt.scenario_id].append(attempt)
    for scenario_id, paired in parity_groups.items():
        persisted_identities = {
            (
                capture_by_attempt_id[item.attempt_id].run_id,
                capture_by_attempt_id[item.attempt_id].persisted_message_id,
            )
            for item in paired
            if capture_by_attempt_id[item.attempt_id].run_id
            and capture_by_attempt_id[item.attempt_id].persisted_message_id is not None
        }
        signatures = {
            (" ".join(item.assistant_text.split()), tuple(item.tool_trajectory))
            for item in paired
        }
        same_persisted_answer = (
            len(paired) == 2
            and len(persisted_identities) == 1
            and all(
                capture_by_attempt_id[item.attempt_id].run_id
                and capture_by_attempt_id[item.attempt_id].persisted_message_id is not None
                for item in paired
            )
            and len({tuple(item.tool_trajectory) for item in paired}) == 1
        )
        if len(paired) != 2 or (not same_persisted_answer and len(signatures) != 1):
            for attempt in paired:
                _append_failure(
                    attempt, failure="transport_parity", taxonomy="TRANSPORT",
                    rationale=(
                        f"Paired stream/nonstream outputs for {scenario_id!r} are not identical."
                    ),
                )

    passed = sum(item.passed for item in attempts)
    return CaptureReview(
        attempts=attempts,
        total_attempts=len(attempts),
        passed_attempts=passed,
        failed_attempts=len(attempts) - passed,
    )
