"""Live generation progress, and real per-step latency on recorded steps.

Two guarantees are load-bearing here:

1. The user gets movement during a long model decode, and
2. the `assistant.progress` event carries no answer prose -- ever. Its payload is
   server-authored, and it stays that way even though draft prose now has its own
   channel (`assistant.delta`, see `test_draft_answer_streaming.py`): progress is
   what the client falls back to when there is no text yet, so it must not become
   a second, unguarded way for model output to reach the screen.

These turns pass no `heartbeat_callback` and leave `stream_draft_answer` off, so
the answer here is delivered post-validation, in one piece.
"""

from __future__ import annotations

import asyncio
import datetime
import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from sqlalchemy import select

from src.db.models import Conversation, QueryUsage, User
from src.db.models.harness import HarnessAgentStepRow
from src.harness.agent_chat_runtime import (
    _StepTimeline,
    execute_production_agent_chat_turn,
)
from src.harness.agent_runs import sanitize_public_payload
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import ManifestScope
from src.harness.readonly_runtime import (
    _LiveProgressReporter,
    build_readonly_primary_runtime,
    run_readonly_primary_turn,
)
from src.harness.registry import default_capability_registry
from src.harness.models import METADATA_QUERY_CAPABILITY, EvidenceQueryResult
from src.harness.trusted_answer import TrustedAnswerSink, TrustedRagAnswer
from src.turn_progress import report_phase
from src.turn_timings import measure_model, measure_search, turn_timings_scope

pytestmark = pytest.mark.asyncio

_GROUNDING = json.dumps(
    {"answer_status": "grounded", "citations": [], "disclosures": []}
)
_ANSWER = "Amsterdam is the answer and this sentence is the secret draft prose."


class _ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del tools, tool_choice, kwargs
        return self


class _SlowStreamingModel(_ScriptedModel):
    """Stream one answer as many small chunks, as a real provider decode does."""

    calls: int = 0

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        del messages, stop, run_manager, kwargs
        self.calls += 1
        text = f"{_ANSWER}\n```nexus-grounding\n{_GROUNDING}\n```"
        for index in range(0, len(text), 8):
            await asyncio.sleep(0.005)
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=text[index : index + 8])
            )


async def _run_turn(model, events: list[tuple[str, dict]]):  # noqa: ANN001, ANN202
    registry = default_capability_registry()
    runtime = build_readonly_primary_runtime(
        model=model,
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=CapabilityExecutionContext(user_id=1, conversation_id=1),
        scope=ManifestScope(include_bounded_loop_only=True),
    )

    async def capture(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    return await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Where is it?",
        event_callback=capture,
    )


async def test_progress_is_emitted_before_any_answer_text_and_carries_no_prose() -> None:
    events: list[tuple[str, dict]] = []
    turn = await _run_turn(
        _ScriptedModel(
            responses=[
                AIMessage(content=f"{_ANSWER}\n```nexus-grounding\n{_GROUNDING}\n```")
            ]
        ),
        events,
    )
    kinds = [kind for kind, _ in events]
    assert "assistant.progress" in kinds
    assert "assistant.delta" in kinds
    assert kinds.index("assistant.progress") < kinds.index("assistant.delta")

    published = [payload for kind, payload in events if kind == "assistant.progress"]
    for payload in published:
        assert "content" not in payload
        # Server-authored only: no fragment of the model's draft may appear, and
        # the sanitizer would drop a `content` key even if one were added later.
        assert _ANSWER[:20] not in json.dumps(payload)
        assert "nexus-grounding" not in json.dumps(payload)
        assert set(sanitize_public_payload("assistant.progress", payload)) <= {
            "status",
            "phase",
            "summary",
            "elapsed_ms",
        }
    # The answer itself is still only delivered post-validation, in full.
    delivered = "".join(
        payload["content"] for kind, payload in events if kind == "assistant.delta"
    )
    assert delivered == turn.grounded.content == _ANSWER


async def test_a_long_decode_reports_the_writing_phase_from_streamed_chunks() -> None:
    events: list[tuple[str, dict]] = []
    await _run_turn(_SlowStreamingModel(responses=[AIMessage(content="unused")]), events)
    phases = [
        payload.get("phase")
        for kind, payload in events
        if kind == "assistant.progress"
    ]
    assert "writing_answer" in phases
    assert "validating_answer" in phases


async def test_progress_is_throttled_so_a_decode_does_not_flood_the_stream() -> None:
    ticks = iter([0.0, 0.1, 0.2, 0.3, 5.0, 5.1])
    emitted: list[dict] = []

    async def capture(kind: str, payload: dict) -> None:
        assert kind == "assistant.progress"
        emitted.append(payload)

    reporter = _LiveProgressReporter(
        capture, clock=lambda: next(ticks), interval=2.0
    )
    for _ in range(5):
        await reporter.note_model_chunk(AIMessageChunk(content="draft text"))
    # The constructor takes the first tick as the turn start. The first chunk then
    # emits, the next three fall inside the interval, and the 5s tick emits again.
    # The elapsed clock is reported, so the status line visibly moves.
    assert len(emitted) == 2
    assert [payload["elapsed_ms"] for payload in emitted] == [100, 5_000]
    assert emitted[-1]["summary"].endswith("5s")


async def test_phase_change_reports_immediately_without_waiting_for_the_interval() -> None:
    ticks = iter([0.0, 0.05, 0.1])
    emitted: list[dict] = []

    async def capture(kind: str, payload: dict) -> None:
        del kind
        emitted.append(payload)

    reporter = _LiveProgressReporter(capture, clock=lambda: next(ticks), interval=99.0)
    await reporter.note_phase("thinking")
    await reporter.note_phase("reading_evidence")
    assert [payload["phase"] for payload in emitted] == ["thinking", "reading_evidence"]


async def test_step_timeline_bills_decode_to_the_step_and_execution_to_the_tool() -> None:
    timeline = _StepTimeline()
    await asyncio.sleep(0.02)
    started, finished, latency = timeline.measure(
        event_type="tool.proposed", call_ref="call-1"
    )
    # `tool.proposed` measures how long the model took to choose the call, so the
    # window it reports is the one it actually spans.
    assert latency >= 15
    assert abs(
        (finished - started) - datetime.timedelta(seconds=latency / 1_000)
    ) < datetime.timedelta(milliseconds=1)
    timeline.note_call_proposed("call-1")
    await asyncio.sleep(0.02)
    _, _, completed_latency = timeline.measure(
        event_type="tool.completed", call_ref="call-1"
    )
    assert completed_latency >= 15
    # An unknown call_ref falls back to the previous boundary rather than to 0,
    # so a step never silently reports "instant".
    _, _, boundary_latency = timeline.measure(
        event_type="tool.completed", call_ref="never-proposed"
    )
    assert boundary_latency >= 0


class _StreamingAnswerModel(_ScriptedModel):
    calls: int = 0

    async def _agenerate(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        self.calls += 1
        await asyncio.sleep(0.02)
        return await super()._agenerate(*args, **kwargs)


async def test_a_live_turn_persists_non_zero_step_and_component_timings(
    db_session, monkeypatch
) -> None:
    from src.config import settings

    user = User(username="timings", email="timings@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Timings", user_id=user.id)
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    model = _StreamingAnswerModel(
        responses=[
            AIMessage(content=f"Safe answer.\n```nexus-grounding\n{_GROUNDING}\n```")
        ]
    )
    monkeypatch.setattr(settings, "agent_runtime_v2_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_v2_canary_stage", "workflow_approvals")
    monkeypatch.setattr(settings, "agent_runtime_v2_effects_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_v2_effect_users", [user.id])
    monkeypatch.setattr(settings, "agent_runtime_v2_live_streaming_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_v2_live_streaming_users", [user.id])
    monkeypatch.setattr(
        "src.harness.agent_chat_runtime.build_readonly_primary_chat_model",
        lambda _settings: model,
    )
    await execute_production_agent_chat_turn(
        db=db_session,
        qdrant=None,  # type: ignore[arg-type]
        conversation=conversation,
        query="Hello",
        images=None,
        client_turn_id="timings-turn",
        requested_project_id=None,
        user_id=user.id,
        user_groups=[],
        project_scope=None,
        permissions=None,
        access_token=None,
        transport="stream",
    )
    steps = (
        await db_session.execute(
            select(HarnessAgentStepRow).order_by(HarnessAgentStepRow.sequence)
        )
    ).scalars().all()
    assert steps
    # Every recorded step now closes: previously only `kind='final'` did.
    assert all(step.finished_at is not None for step in steps)
    assert all(step.started_at <= step.finished_at for step in steps)
    non_final = [step for step in steps if step.kind != "final"]
    assert non_final
    assert any(step.latency_ms > 0 for step in non_final)

    usage = (
        await db_session.execute(
            select(QueryUsage).where(QueryUsage.interface == "conversation_agent")
        )
    ).scalar_one()
    assert usage.llm_ms is not None and usage.llm_ms > 0
    # No retrieval ran in this turn, so those two are a real measured zero rather
    # than the NULL they used to be.
    assert usage.embedding_ms == 0
    assert usage.search_ms == 0


# --- Progress during long-running tool calls -------------------------------
#
# The failure these cover: on the dominant chat path the outer agent model
# finishes in ~2s (it only emits a tool call) and a single
# `answer_from_documentation` call then runs for ~37s. A heartbeat driven by
# streamed model chunks has no clock during that window, so the status line froze
# for 95% of the turn.



def _tool_calling_model(*, tool_id: str = "slow-call") -> _ScriptedModel:
    return _ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_project_metadata",
                        "args": {"query_type": "project_list"},
                        "id": tool_id,
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=f"Answer from the tool.\n```nexus-grounding\n{_GROUNDING}\n```"
            ),
        ]
    )


async def _run_turn_with_slow_tool(
    monkeypatch,  # noqa: ANN001
    *,
    tool_seconds: float,
    interval: float,
    inner_phases: tuple[str, ...] = (),
) -> list[tuple[str, str, dict]]:
    """Run one turn whose only work is a slow tool; return the ordered events."""

    monkeypatch.setattr(
        "src.harness.readonly_runtime.PROGRESS_INTERVAL_SECONDS", interval
    )
    timeline: list[tuple[str, str, dict]] = []

    async def slow_handler(arguments, context):  # noqa: ANN001
        del arguments, context
        for phase in inner_phases:
            await report_phase(phase)
        await asyncio.sleep(tool_seconds)
        return EvidenceQueryResult(
            capability_id=METADATA_QUERY_CAPABILITY.id, status="succeeded", items=[]
        )

    registry = default_capability_registry()
    runtime = build_readonly_primary_runtime(
        model=_tool_calling_model(),
        registry=registry,
        executor=HarnessCapabilityExecutor(
            registry, evidence_handlers={METADATA_QUERY_CAPABILITY.id: slow_handler}
        ),
        context=CapabilityExecutionContext(user_id=1, conversation_id=1),
        scope=ManifestScope(include_bounded_loop_only=True),
    )

    async def capture(kind: str, payload: dict) -> None:
        timeline.append(("event", kind, payload))

    async def capture_heartbeat(kind: str, payload: dict) -> None:
        timeline.append(("heartbeat", kind, payload))

    await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="List projects",
        event_callback=capture,
        heartbeat_callback=capture_heartbeat,
    )
    return timeline


async def test_heartbeat_keeps_ticking_through_a_long_tool_call(monkeypatch) -> None:
    timeline = await _run_turn_with_slow_tool(
        monkeypatch, tool_seconds=0.5, interval=0.05
    )
    kinds = [(source, kind) for source, kind, _ in timeline]
    proposed = kinds.index(("event", "tool.proposed"))
    completed = kinds.index(("event", "tool.completed"))
    during_tool = [
        payload
        for (source, kind, payload) in timeline[proposed + 1 : completed]
        if source == "heartbeat"
    ]
    # The model emitted no chunks during this window, so every one of these came
    # from the elapsed-time ticker.
    assert len(during_tool) >= 3, kinds
    # The ticker refreshes the clock without inventing a phase: it re-publishes
    # the phase the proposal named, which for this capability is the specific
    # source being read rather than the generic `reading_evidence`.
    assert {payload["phase"] for payload in during_tool} == {"reading_project_metadata"}
    assert [payload["elapsed_ms"] for payload in during_tool] == sorted(
        payload["elapsed_ms"] for payload in during_tool
    )


async def test_heartbeats_carry_no_answer_content_and_only_allowlisted_fields(
    monkeypatch,
) -> None:
    timeline = await _run_turn_with_slow_tool(
        monkeypatch, tool_seconds=0.3, interval=0.05
    )
    beats = [payload for source, _kind, payload in timeline if source == "heartbeat"]
    assert beats
    for payload in beats:
        assert "content" not in payload
        assert "Answer from the tool" not in json.dumps(payload)
        assert set(sanitize_public_payload("assistant.progress", payload)) <= {
            "status",
            "phase",
            "summary",
            "elapsed_ms",
        }


async def test_inner_work_can_name_its_own_phase_from_inside_a_tool_call(
    monkeypatch,
) -> None:
    timeline = await _run_turn_with_slow_tool(
        monkeypatch,
        tool_seconds=0.3,
        interval=0.05,
        inner_phases=("searching_documents", "generating_answer"),
    )
    progress = [
        (source, payload)
        for source, kind, payload in timeline
        if kind == "assistant.progress"
    ]
    phases = [payload["phase"] for _source, payload in progress]
    assert "searching_documents" in phases
    assert "generating_answer" in phases

    # Once the work announces it is generating, every later heartbeat must report
    # THAT phase: the ticker refreshes the clock and must not revert to a generic
    # label or invent a phase of its own.
    first_generating = phases.index("generating_answer")
    later_beats = [
        payload["phase"]
        for source, payload in progress[first_generating + 1 :]
        if source == "heartbeat"
    ]
    assert later_beats, phases
    assert set(later_beats) == {"generating_answer"}

    summaries = {payload["summary"].split("...")[0] for _source, payload in progress}
    assert "Writing the answer from the sources" in summaries
    assert "Searching the documentation" in summaries


async def test_the_ticker_stops_when_the_turn_ends(monkeypatch) -> None:
    timeline = await _run_turn_with_slow_tool(
        monkeypatch, tool_seconds=0.1, interval=0.02
    )
    before = len([1 for source, _kind, _payload in timeline if source == "heartbeat"])
    await asyncio.sleep(0.15)
    after = len([1 for source, _kind, _payload in timeline if source == "heartbeat"])
    # A heartbeat arriving after the answer would describe a turn that is over.
    assert before == after


async def test_no_heartbeat_callback_means_no_ticker(monkeypatch) -> None:
    """The ticker needs an independent emitter; without one it must not run."""

    monkeypatch.setattr("src.harness.readonly_runtime.PROGRESS_INTERVAL_SECONDS", 0.02)
    events: list[tuple[str, dict]] = []
    turn = await _run_turn(
        _ScriptedModel(
            responses=[
                AIMessage(content=f"Direct.\n```nexus-grounding\n{_GROUNDING}\n```")
            ]
        ),
        events,
    )
    assert turn.grounded.content == "Direct."
    # Phase-change events still flow inline; only the timer is absent.
    assert any(kind == "assistant.progress" for kind, _ in events)


# --- Nested LLM attribution ------------------------------------------------


async def test_nested_model_time_lands_in_llm_ms_not_in_opaque_tool_time() -> None:
    async def rag_like_tool() -> None:
        # Shape of `answer_from_documentation`: a little retrieval, then a long
        # nested generation that the harness middleware never sees.
        with measure_model():
            await asyncio.sleep(0.05)

    with turn_timings_scope() as timings:
        await rag_like_tool()
    assert timings.model_ms >= 40
    assert timings.model_calls == 1


async def test_model_time_inside_retrieval_is_not_billed_to_search_as_well() -> None:
    with turn_timings_scope() as timings:
        with measure_search():
            # Query expansion calls an LLM from inside retrieval.
            with measure_model():
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.01)
    assert timings.model_ms >= 40
    # Search keeps only its own time, so the two columns remain summable.
    assert timings.search_ms < 40


async def test_the_trusted_answer_path_does_not_claim_a_validation_that_never_ran(
    monkeypatch,
) -> None:
    """`answer_from_documentation` bypasses the grounding validator by design.

    It returns a deterministically cited answer, so the correction loop is skipped.
    Reporting a `validating_answer` phase there would describe work that never
    happened.
    """

    monkeypatch.setattr("src.harness.readonly_runtime.PROGRESS_INTERVAL_SECONDS", 0.05)
    sink = TrustedAnswerSink()
    sink.answer = TrustedRagAnswer(content="Deterministic answer.", document_sources=[])
    registry = default_capability_registry()
    runtime = build_readonly_primary_runtime(
        model=_ScriptedModel(responses=[AIMessage(content="unused")]),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=CapabilityExecutionContext(user_id=1, conversation_id=1),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    events: list[tuple[str, dict]] = []

    async def capture(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Summarize the documentation",
        event_callback=capture,
        heartbeat_callback=capture,
        trusted_answer_sink=sink,
    )
    assert turn.grounded.content == "Deterministic answer."
    phases = [
        payload["phase"] for kind, payload in events if kind == "assistant.progress"
    ]
    assert "validating_answer" not in phases
    assert phases[-1] == "finalizing_answer"
