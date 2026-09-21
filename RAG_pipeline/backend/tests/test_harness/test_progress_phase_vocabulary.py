"""What the live status line says during a turn, and why each sentence is true.

The turn's only honest live signal is ``assistant.progress``: the answer itself
is withheld until the grounding validator accepts it. These tests pin the two
properties that make that signal worth showing.

* **Truthful.** Every phase and every summary is derived from state that is
  actually in flight -- the capability the executor is running, the operation it
  was asked for, a count the topology tools computed server-side. A wrong label
  is worse than a vague one, because the user believes it, so a capability with
  no human-facing source name degrades to the generic phase instead of guessing.
* **Safe.** ``summary`` is a public payload composed only of module constants and
  integers. No model text, no tool arguments, no observation prose -- and it goes
  through the same allowlist and credential redaction as every durable event.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from src.harness.agent_runs import _PUBLIC_EVENT_FIELDS, sanitize_public_payload
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import ManifestScope
from src.harness.readonly_runtime import (
    _GENERIC_PROGRESS_LABEL,
    _MAX_PROGRESS_LABEL_CHARS,
    _PROGRESS_LABELS,
    _TOPOLOGY_OPERATION_VERBS,
    _VISIBLE_TOOL_LABELS,
    _ambiguous_candidate_count,
    _LiveProgressReporter,
    _post_read_phase,
    _read_batch_phase,
    _source_read_phase,
    build_readonly_primary_runtime,
    run_readonly_primary_turn,
)
from src.harness.registry import default_capability_registry
from src.harness.semantic_tools import (
    ToolEnvelope,
    build_semantic_tool_snapshot,
)
from src.harness.agent_loop import AgentStopReason
from src.harness.topology_capability import TOPOLOGY_STRUCTURE_CAPABILITY
from src.runtime.models import RuntimeToolResult
from src.topology.client import TOPOLOGY_TOOL_NAMES

# No module-level asyncio mark: most of the vocabulary is pure functions, and
# `asyncio_mode = "auto"` already collects the async cases.
_TOPOLOGY_NAME = TOPOLOGY_STRUCTURE_CAPABILITY.semantic_name
#: A grounding block that cites the one reference a successful topology read
#: publishes, so the validator accepts the answer and the answer path under test
#: is the real one rather than the safe fallback.
_GROUNDING = json.dumps(
    {
        "answer_status": "grounded",
        "citations": [
            {"claim_key": "dependency", "evidence_refs": ["topology:project-a"]}
        ],
        "disclosures": [],
    }
)
#: A credential planted where observation-derived text would come from, if any of
#: it were allowed to reach the payload. None of it is.
_PLANTED = "api_key=sk-live-abcdef0123456789"


# --- The derived vocabulary -------------------------------------------------
#
# Phases are pinned rather than recomputed: the client keys on them, and they are
# derived from the humanizer's source labels, so a label rename silently renames
# a phase. That is a frontend contract change and should fail here first.
_EXPECTED_SOURCE_PHASES = {
    "query_project_metadata": ("reading_project_metadata", "Reading project metadata"),
    "search_documentation": (
        "reading_indexed_documentation",
        "Reading indexed documentation",
    ),
    "list_generated_documents": (
        "reading_generated_document_inventory",
        "Reading generated document inventory",
    ),
    "search_within_document": ("reading_indexed_document", "Reading indexed document"),
    "read_document": (
        "reading_ordered_indexed_document",
        "Reading ordered indexed document",
    ),
    "read_generated_document": (
        "reading_generated_document",
        "Reading generated document",
    ),
    "read_topology_structure": (
        "reading_project_topology",
        "Reading the project topology",
    ),
}


def _readonly_semantic_names() -> set[str]:
    snapshot = build_semantic_tool_snapshot(
        default_capability_registry(),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    return {record.semantic_name for record in snapshot.tools}


def test_every_labelled_source_is_a_capability_that_actually_exists() -> None:
    """A label keyed on a name no capability answers to is dead weight.

    This is the check the suite was missing. The expectation table above was
    pinned against `_VISIBLE_TOOL_LABELS` and against nothing else, so the two
    agreed with each other while both disagreed with the registry: when
    `read_document` was split into `search_within_document` and
    the ordered document reader, the stale key kept the test green and BOTH new tools
    silently lost their progress phase, degrading a nameable read to the generic
    "reading evidence" line.
    """

    registered = _readonly_semantic_names()
    dead = set(_VISIBLE_TOOL_LABELS) - registered
    assert not dead, f"labels key on capabilities that no longer exist: {sorted(dead)}"


def test_two_sources_never_collapse_onto_one_phase() -> None:
    """Phases are derived by slugifying the label, so two labels can collide.

    The client keys on the phase string, so a collision makes two different reads
    indistinguishable on the status line -- and it is invisible at the point of
    edit, because the labels themselves still read differently.
    """

    phases = [
        _source_read_phase(name, None).phase  # type: ignore[union-attr]
        for name in _VISIBLE_TOOL_LABELS
    ]
    assert len(set(phases)) == len(phases), f"colliding phases: {sorted(phases)}"


def test_every_humanized_source_derives_a_stable_phase_and_label() -> None:
    assert set(_EXPECTED_SOURCE_PHASES) == set(_VISIBLE_TOOL_LABELS)
    for semantic_name, (phase, label) in _EXPECTED_SOURCE_PHASES.items():
        state = _source_read_phase(semantic_name, None)
        assert state is not None
        assert (state.phase, state.label) == (phase, label)
        # The semantic name is an implementation detail: it may not appear in
        # either half of the payload, not even slugified into the phase.
        assert semantic_name not in state.phase
        assert semantic_name not in state.label


def test_topology_operations_name_the_work_they_actually_do() -> None:
    # A renamed or removed dispatcher operation must fail here rather than
    # silently degrade every topology turn to the generic label.
    assert set(_TOPOLOGY_OPERATION_VERBS) <= TOPOLOGY_TOOL_NAMES

    searching = _source_read_phase(_TOPOLOGY_NAME, {"operation": "find_topology_node"})
    traversing = _source_read_phase(
        _TOPOLOGY_NAME, {"operation": "list_topology_dependencies"}
    )
    assert searching is not None and traversing is not None
    assert (searching.phase, searching.label) == (
        "searching_project_topology",
        "Searching the project topology",
    )
    assert (traversing.phase, traversing.label) == (
        "traversing_project_topology",
        "Traversing the project topology",
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"operation": "get_job_detail"},
        {"operation": "not-a-real-operation"},
        {"operation": _PLANTED},
        {"operation": 17},
        {},
        None,
    ],
)
def test_an_unrecognized_topology_operation_degrades_to_reading(arguments) -> None:  # noqa: ANN001
    state = _source_read_phase(_TOPOLOGY_NAME, arguments)

    assert state is not None
    # True of every topology call, so it is the safe floor. A model-supplied
    # operation is matched against a closed allowlist, never echoed.
    assert (state.phase, state.label) == _EXPECTED_SOURCE_PHASES[_TOPOLOGY_NAME]
    assert "sk-live" not in state.label


def test_a_capability_with_no_friendly_name_never_leaks_its_semantic_name() -> None:
    snapshot = build_semantic_tool_snapshot(
        default_capability_registry(),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    unlabelled = [
        record.semantic_name
        for record in snapshot.tools
        if record.semantic_name not in _VISIBLE_TOOL_LABELS
    ]
    assert unlabelled, "the read-only surface should still have unlabelled tools"

    for semantic_name in unlabelled:
        assert _source_read_phase(semantic_name, None) is None
        state = _read_batch_phase([(semantic_name, "call-1", {})])
        assert (state.phase, state.label) == (
            "reading_evidence",
            _PROGRESS_LABELS["reading_evidence"],
        )
        assert semantic_name not in state.phase
        assert semantic_name not in state.label


def test_a_mixed_batch_of_reads_names_neither_call() -> None:
    state = _read_batch_phase(
        [
            ("query_project_metadata", "call-1", {}),
            (_TOPOLOGY_NAME, "call-2", {"operation": "find_topology_node"}),
        ]
    )

    # Both are in flight; naming either one would be a coin flip.
    assert state.phase == "reading_evidence"


def test_two_calls_of_one_capability_keep_the_source_but_drop_the_operation() -> None:
    state = _read_batch_phase(
        [
            (_TOPOLOGY_NAME, "call-1", {"operation": "find_topology_node"}),
            (_TOPOLOGY_NAME, "call-2", {"operation": "list_topology_dependencies"}),
        ]
    )

    assert (state.phase, state.label) == _EXPECTED_SOURCE_PHASES[_TOPOLOGY_NAME]


# --- Ambiguity counts ------------------------------------------------------


def _ambiguous_envelope(count: int, *, flag: str = "unresolved") -> ToolEnvelope:
    """One topology observation that refused to guess between ``count`` names."""

    content: dict[str, object] = {
        "project_id": "project-a",
        "query": _PLANTED,
        "candidates": [
            {"id": f"node-{index}", "name": f"{_PLANTED} {index}"}
            for index in range(count)
        ],
    }
    if flag == "unresolved":
        content["resolved"] = False
        content["reason"] = "ambiguous"
    else:
        content["ambiguous"] = True
    return ToolEnvelope(
        status="succeeded",
        summary=f"Ambiguous name; {_PLANTED}",
        data={"content": content, "targets": []},
    )


@pytest.mark.parametrize("flag", ["unresolved", "ambiguous"])
def test_a_server_flagged_ambiguity_is_counted(flag: str) -> None:
    assert _ambiguous_candidate_count(_ambiguous_envelope(4, flag=flag).data) == 4


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"content": "not-a-mapping"},
        # Resolved cleanly: candidates listed, but nothing to disambiguate.
        {"content": {"resolved": True, "candidates": [{"id": "a"}, {"id": "b"}]}},
        # A search result that is not flagged ambiguous is not an ambiguity.
        {"content": {"candidates": [{"id": "a"}, {"id": "b"}], "match_count": 2}},
        # "Resolving 1 candidate match" is not a thing.
        {"content": {"resolved": False, "reason": "ambiguous", "candidates": [{"id": "a"}]}},
        {"content": {"resolved": False, "reason": "not_found", "candidates": []}},
    ],
)
def test_an_unflagged_result_is_never_reported_as_an_ambiguity(data: dict) -> None:
    assert _ambiguous_candidate_count(data) is None


def test_a_planted_credential_in_an_observation_never_reaches_the_label() -> None:
    state = _post_read_phase(_ambiguous_envelope(3))

    assert (state.phase, state.label) == (
        "resolving_candidates",
        "Resolving 3 candidate matches",
    )
    # Candidate names, the query and the envelope summary all carried the
    # credential. Only the count crosses the boundary.
    assert "sk-live" not in state.label
    assert _PLANTED not in state.label


def test_a_plain_read_hands_control_back_to_the_model() -> None:
    plain = ToolEnvelope(status="succeeded", summary="Read.", data={"items": []})

    for envelope in (plain, None):
        state = _post_read_phase(envelope)
        assert (state.phase, state.label) == ("thinking", _PROGRESS_LABELS["thinking"])


# --- The payload boundary --------------------------------------------------


async def _emitted_payloads(
    label: str | None, *, phase: str = "reading_evidence"
) -> list[dict]:
    emitted: list[dict] = []
    ticks = iter([0.0, 3.0, 9.0])

    async def capture(kind: str, payload: dict) -> None:
        assert kind == "assistant.progress"
        emitted.append(payload)

    reporter = _LiveProgressReporter(
        capture, heartbeat_callback=capture, clock=lambda: next(ticks), interval=1.0
    )
    await reporter.note_phase(phase, label=label)
    await reporter.beat()
    return emitted


async def test_a_summary_is_bounded_and_credential_redacted() -> None:
    hostile = "Reading " + _PLANTED + " " + ("x" * 500)

    payloads = await _emitted_payloads(hostile)

    assert payloads
    for payload in payloads:
        summary = payload["summary"]
        assert "sk-live" not in summary
        assert "[REDACTED]" in summary
        # The label half is capped; the elapsed suffix adds a handful of chars.
        assert len(summary) <= _MAX_PROGRESS_LABEL_CHARS + 16


async def test_progress_payloads_carry_only_allowlisted_fields() -> None:
    payloads = await _emitted_payloads(None, phase="validating_answer")

    assert payloads
    for payload in payloads:
        assert set(payload) == _PUBLIC_EVENT_FIELDS["assistant.progress"]
        assert "content" not in payload
        # Already sanitized at construction, so the durable event's own gate is a
        # no-op rather than a silent rewrite.
        assert sanitize_public_payload("assistant.progress", payload) == payload


async def test_the_heartbeat_republishes_the_derived_label_without_recomputing() -> None:
    payloads = await _emitted_payloads("Resolving 4 candidate matches")

    # Two emissions: the phase change, then the ticker refreshing the clock. The
    # ticker re-publishes the stored label -- it never re-derives it, so a beat
    # costs nothing regardless of how large the observation was.
    assert len(payloads) == 2
    assert [payload["summary"] for payload in payloads] == [
        "Resolving 4 candidate matches... 3s",
        "Resolving 4 candidate matches... 9s",
    ]
    assert [payload["elapsed_ms"] for payload in payloads] == [3_000, 9_000]


async def test_an_unrecognized_phase_reports_vaguely_rather_than_guessing() -> None:
    payloads = await _emitted_payloads(None, phase="reranking_passages")

    # No layer of this module knows what that phase is, so it may not borrow the
    # sharper "waiting on the model" wording, which is only true of `thinking`.
    assert payloads[0]["summary"].startswith(_GENERIC_PROGRESS_LABEL)
    assert _GENERIC_PROGRESS_LABEL != _PROGRESS_LABELS["thinking"]


# --- One live turn --------------------------------------------------------


class _ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del tools, tool_choice, kwargs
        return self


def _topology_turn_model(operation: str) -> _ScriptedModel:
    arguments = {"operation": operation, "project_id": "project-a"}
    if operation in {
        "get_topology_node",
        "list_topology_dependencies",
        "get_job_detail",
    }:
        arguments["name_or_id"] = "order job"
    elif operation == "find_topology_node":
        arguments["query"] = "order job"
    elif operation == "get_component_detail":
        arguments.update({"job": "order job", "component": "tMap_1"})
    return _ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": _TOPOLOGY_NAME,
                        "args": arguments,
                        "id": "topology-call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=(
                    "The order job feeds the warehouse system.\n"
                    f"```nexus-grounding\n{_GROUNDING}\n```"
                )
            ),
        ]
    )


async def _run_topology_turn(
    monkeypatch, *, operation: str, content: dict
) -> tuple[object, list[tuple[str, dict]]]:
    """Run one real turn whose single tool call is a patched topology read."""

    async def fake_execute(call, *, project_scope, access_token):  # noqa: ANN001, ANN202
        del project_scope, access_token
        return RuntimeToolResult(
            tool_name=call.tool_name,
            success=True,
            data={"content": content, "targets": []},
            error=None,
            latency_ms=1,
            truncated=False,
        )

    monkeypatch.setattr(
        "src.harness.topology_capability.execute_topology_tool", fake_execute
    )
    registry = default_capability_registry()
    runtime = build_readonly_primary_runtime(
        model=_topology_turn_model(operation),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=CapabilityExecutionContext(
            user_id=1, conversation_id=1, project_scope=["project-a"]
        ),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    events: list[tuple[str, dict]] = []

    async def capture(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="What depends on the order job?",
        event_callback=capture,
    )
    return turn, events


def _progress(events: list[tuple[str, dict]]) -> list[dict]:
    return [payload for kind, payload in events if kind == "assistant.progress"]


async def test_a_topology_read_in_flight_names_the_topology_and_its_operation(
    monkeypatch,
) -> None:
    _turn, events = await _run_topology_turn(
        monkeypatch,
        operation="list_topology_dependencies",
        content={"project_id": "project-a", "resolved": True, "dependencies": []},
    )

    published = _progress(events)
    phases = [payload["phase"] for payload in published]
    assert "traversing_project_topology" in phases
    summaries = {payload["summary"].split("...")[0] for payload in published}
    assert "Traversing the project topology" in summaries
    # The generic read label is not published when the source is known.
    assert "reading_evidence" not in phases
    # And the phase is reported while the call is in flight, before its result.
    kinds = [kind for kind, _payload in events]
    first_traverse = next(
        index
        for index, (kind, payload) in enumerate(events)
        if kind == "assistant.progress"
        and payload["phase"] == "traversing_project_topology"
    )
    assert first_traverse < kinds.index("tool.completed")


async def test_an_ambiguous_topology_result_reports_the_real_candidate_count(
    monkeypatch,
) -> None:
    _turn, events = await _run_topology_turn(
        monkeypatch,
        operation="get_topology_node",
        content={
            "project_id": "project-a",
            "resolved": False,
            "reason": "ambiguous",
            "candidates": [{"id": f"node-{index}"} for index in range(3)],
        },
    )

    published = _progress(events)
    assert "resolving_candidates" in [payload["phase"] for payload in published]
    assert "Resolving 3 candidate matches" in {
        payload["summary"].split("...")[0] for payload in published
    }


async def test_progress_labelling_leaves_the_answer_path_untouched(
    monkeypatch,
) -> None:
    turn, events = await _run_topology_turn(
        monkeypatch,
        operation="list_topology_dependencies",
        content={"project_id": "project-a", "resolved": True, "dependencies": []},
    )

    # Deltas: still one post-validation delivery of exactly the grounded answer.
    delivered = "".join(
        payload["content"] for kind, payload in events if kind == "assistant.delta"
    )
    assert delivered == turn.grounded.content
    assert "The order job feeds the warehouse system." in delivered
    assert turn.result.stop_reason is AgentStopReason.ANSWERED
    assert turn.used_fallback is False

    # Validation still ran and is still named, and no draft prose leaked into any
    # progress payload on the way.
    published = _progress(events)
    assert "validating_answer" in [payload["phase"] for payload in published]
    for payload in published:
        assert set(payload) == _PUBLIC_EVENT_FIELDS["assistant.progress"]
        assert "warehouse" not in json.dumps(payload)
        assert "nexus-grounding" not in json.dumps(payload)
    # Progress still leads the answer, and no progress event follows it.
    kinds = [kind for kind, _payload in events]
    assert kinds.index("assistant.progress") < kinds.index("assistant.delta")
    assert "assistant.progress" not in kinds[kinds.index("assistant.delta") :]
