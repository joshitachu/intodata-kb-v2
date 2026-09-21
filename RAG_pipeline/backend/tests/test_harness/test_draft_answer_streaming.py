"""Streaming the outer model's answer prose, and the guards that bound it.

Unlike the trusted documentation answer (see `test_trusted_answer_streaming.py`),
this text has NOT passed the grounding validator when it is shown. It is published
provisionally -- `replayable=False` -- and the authoritative post-persistence
replay resets the client buffer and rebuilds it, so what the server stored always
wins on screen.

Provisional is not unfiltered. These tests pin the three transforms that sit
between a raw model chunk and a character a user may read: the grounding block is
never shown, the humanizer is applied to every emitted character, and text that
trips the validator's hard leak rule is never published at all.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import ManifestScope
from src.harness.readonly_runtime import (
    _DraftAnswerStreamer,
    _humanize_visible_content,
    build_readonly_primary_runtime,
    run_readonly_primary_turn,
)
from src.harness.registry import default_capability_registry
from src.harness.trusted_answer import ANSWER_DELTA_CHARS

pytestmark = pytest.mark.asyncio


class _Recorder:
    """Collect published deltas and reset notifications in order."""

    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.resets = 0

    async def publish(self, text: str) -> None:
        self.deltas.append(text)

    async def notify(self) -> None:
        self.resets += 1

    @property
    def shown(self) -> str:
        return "".join(self.deltas)


def _streamer(recorder: _Recorder, *, suppress: bool = False) -> _DraftAnswerStreamer:
    return _DraftAnswerStreamer(recorder.publish, suppress_structural=suppress)


async def _feed_tokens(
    streamer: _DraftAnswerStreamer, text: str, *, size: int = 7, flush: bool = True
) -> None:
    """Feed ``text`` one small chunk at a time, as a provider would."""

    for index in range(0, len(text), size):
        await streamer.feed(text[index : index + size], [])
    if flush:
        await streamer.feed("", [], flush=True)


# --- What reaches the screen ----------------------------------------------


async def test_prose_streams_and_reassembles_to_the_humanized_answer() -> None:
    recorder = _Recorder()
    streamer = _streamer(recorder)
    prose = (
        "The order sync job reads from the staging tables and writes to the "
        "warehouse. It runs nightly, and the last run completed successfully "
        "with no rejected records.\n"
    )

    await _feed_tokens(streamer, prose)

    assert recorder.shown == _humanize_visible_content(prose, [])
    # Batched, not one event per token: a long answer must not become hundreds
    # of durable events.
    assert len(recorder.deltas) < len(prose) / (ANSWER_DELTA_CHARS / 2)
    assert recorder.resets == 0


async def test_grounding_block_is_never_shown() -> None:
    recorder = _Recorder()
    streamer = _streamer(recorder)
    prose = "The job runs nightly and last completed without errors.\n"
    block = (
        '```nexus-grounding\n{"answer_status": "grounded", "citations": '
        '[{"claim_key": "schedule", "evidence_refs": ["metadata:job.order_sync"]}]}\n```'
    )

    await _feed_tokens(streamer, prose + block)

    assert recorder.shown == prose
    assert "nexus-grounding" not in recorder.shown
    assert "evidence_refs" not in recorder.shown
    assert "metadata:job.order_sync" not in recorder.shown


async def test_a_partial_fence_is_never_shown() -> None:
    """The fence arrives token by token; its first backticks must not flash."""

    recorder = _Recorder()
    streamer = _streamer(recorder)
    prose = "x" * (ANSWER_DELTA_CHARS * 2) + " done.\n"

    # Stop mid-fence: the model has emitted "``" but not the third backtick.
    await _feed_tokens(streamer, prose + "``", flush=False)

    assert "`" not in recorder.shown


async def test_evidence_keys_are_humanized_and_never_split_mid_token() -> None:
    recorder = _Recorder()
    streamer = _streamer(recorder)
    # The key is long enough that a naive character-count batch would cut it.
    prose = (
        "The schedule comes from metadata:job.order_sync.nightly.schedule and the "
        "row counts come from metadata:job.order_sync.nightly.metrics, which agree.\n"
    )

    await _feed_tokens(streamer, prose)

    assert recorder.shown == _humanize_visible_content(prose, [])
    assert "metadata:" not in recorder.shown
    assert "project metadata" in recorder.shown


async def test_semantic_tool_names_are_relabelled_before_display() -> None:
    recorder = _Recorder()
    streamer = _streamer(recorder)
    # The two names the old `read_document` capability was split into. The
    # fixture named the retired one, so this case kept passing against a label
    # map that no longer covered either replacement.
    prose = (
        "I used search_documentation to find the runbook, then "
        "search_within_document for the section on retries and "
        "read_document to count them, and all agree on the schedule.\n"
    )

    await _feed_tokens(streamer, prose)

    assert "search_documentation" not in recorder.shown
    assert "search_within_document" not in recorder.shown
    assert "read_document" not in recorder.shown
    assert "indexed documentation" in recorder.shown


async def test_a_whole_document_evidence_id_never_reaches_the_reader() -> None:
    """`doctext:` ids were minted by the whole-document read and scrubbed by
    nothing: the namespace was added upstream and mirrored into neither the
    validator's pattern nor this scrubber's."""

    recorder = _Recorder()
    streamer = _streamer(recorder)
    prose = "The mapping carries 31 fields (doctext:44:2), not 30.\n"

    await _feed_tokens(streamer, prose)

    assert "doctext:" not in recorder.shown
    assert "31 fields" in recorder.shown


# --- The topology surface --------------------------------------------------


async def test_topology_surface_suppresses_structural_references() -> None:
    recorder = _Recorder()
    streamer = _streamer(recorder, suppress=True)
    prose = (
        "The order sync job feeds the warehouse loader (from the project topology).\n"
        "Both run in the nightly batch (from topology:job.order_sync).\n"
    )

    await _feed_tokens(streamer, prose)

    assert recorder.shown == _humanize_visible_content(
        prose, [], suppress_structural=True
    )
    assert "topology" not in recorder.shown.casefold()


async def test_topology_surface_holds_an_incomplete_line() -> None:
    """Suppression matches multi-word spans, so only a whole line is safe.

    Emitting "(from the" and then having to delete it is not possible -- shown
    characters cannot be retracted -- so the line is held until it is complete.
    """

    recorder = _Recorder()
    streamer = _streamer(recorder, suppress=True)
    prose = "A" * (ANSWER_DELTA_CHARS * 3) + " (from the project"

    await _feed_tokens(streamer, prose, flush=False)

    assert recorder.shown == ""


# --- The leak gate ---------------------------------------------------------


async def test_internal_output_is_never_published_and_halts_the_stream() -> None:
    """The one grounding failure that discards an answer outright.

    If the validator would refuse to show this text at all, it must not have been
    shown while it was being written either.
    """

    recorder = _Recorder()
    streamer = _streamer(recorder)
    prose = (
        "The lookup went through rag.docs.answer_question against the index, "
        "which returned the nightly schedule for the order sync job.\n"
    )

    await _feed_tokens(streamer, prose)

    assert "rag.docs.answer_question" not in recorder.shown
    assert recorder.shown == ""


async def test_a_halted_stream_stays_halted() -> None:
    recorder = _Recorder()
    streamer = _streamer(recorder)

    await _feed_tokens(streamer, "Internal call mcp.projects.publish_index ran.\n")
    assert recorder.shown == ""

    await _feed_tokens(streamer, "This later sentence is perfectly safe.\n")
    assert recorder.shown == ""


# --- Retraction ------------------------------------------------------------


async def test_reset_retracts_a_shown_draft_and_notifies_once() -> None:
    recorder = _Recorder()
    streamer = _streamer(recorder)
    await _feed_tokens(streamer, "Let me look that up for you first.\n")
    assert recorder.shown

    await streamer.reset(notify=recorder.notify)

    assert recorder.resets == 1
    assert streamer.streamed == ""


async def test_reset_is_silent_when_nothing_was_shown() -> None:
    """No retraction event for a draft the user never saw."""

    recorder = _Recorder()
    streamer = _streamer(recorder)

    await streamer.reset(notify=recorder.notify)

    assert recorder.resets == 0
    assert recorder.deltas == []


async def test_the_stream_after_a_reset_is_independent_of_the_retracted_draft() -> None:
    recorder = _Recorder()
    streamer = _streamer(recorder)
    await _feed_tokens(streamer, "Checking the topology for that job now.\n")
    await streamer.reset(notify=recorder.notify)
    boundary = len(recorder.shown)

    answer = "The order sync job runs nightly and last completed cleanly.\n"
    await _feed_tokens(streamer, answer)

    assert recorder.shown[boundary:] == _humanize_visible_content(answer, [])


async def test_a_reset_clears_a_halt_so_the_replacement_can_stream() -> None:
    """A leak in a discarded draft must not silence the corrected answer."""

    recorder = _Recorder()
    streamer = _streamer(recorder)
    await _feed_tokens(streamer, "Ran mcp.projects.publish_index for this.\n")
    assert recorder.shown == ""

    await streamer.reset(notify=recorder.notify)
    answer = "The index was published for the order sync project.\n"
    await _feed_tokens(streamer, answer)

    assert recorder.shown == _humanize_visible_content(answer, [])


# --- Failure isolation -----------------------------------------------------


async def test_a_publish_failure_never_reaches_the_caller() -> None:
    """Display is cosmetic. A transport failure must not fail the turn."""

    async def failing_publish(_text: str) -> None:
        raise RuntimeError("event channel is gone")

    streamer = _DraftAnswerStreamer(failing_publish)

    await _feed_tokens(streamer, "A perfectly ordinary sentence about the job.\n")


# --- Through the real turn loop -------------------------------------------


def _grounding(**overrides) -> str:  # noqa: ANN003
    block = {"answer_status": "grounded", "citations": [], "disclosures": []}
    block.update(overrides)
    return f"```nexus-grounding\n{json.dumps(block)}\n```"


class _StreamingScriptedModel(FakeMessagesListChatModel):
    """Stream one scripted text per model call, in chunks, as a provider does."""

    texts: list[str] = []
    calls: int = 0

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del tools, tool_choice, kwargs
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        del messages, stop, run_manager, kwargs
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        for index in range(0, len(text), 8):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=text[index : index + 8])
            )


async def _run_turn(texts: list[str], *, stream_draft: bool = True):  # noqa: ANN202
    """Run one real read-only turn, recording both public channels separately."""

    registry = default_capability_registry()
    runtime = build_readonly_primary_runtime(
        model=_StreamingScriptedModel(responses=[AIMessage(content="unused")], texts=texts),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=CapabilityExecutionContext(user_id=1, conversation_id=1),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    durable: list[tuple[str, dict]] = []
    live: list[tuple[str, dict]] = []

    async def on_event(kind: str, payload: dict) -> None:
        durable.append((kind, payload))

    async def on_heartbeat(kind: str, payload: dict) -> None:
        live.append((kind, payload))

    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="When does the order sync job run?",
        event_callback=on_event,
        heartbeat_callback=on_heartbeat,
        # As production does: the authoritative replay is the CALLER's job, after
        # validation and persistence, so the turn itself emits no durable prose.
        emit_answer_deltas=False,
        stream_draft_answer=stream_draft,
    )
    return turn, durable, live


async def test_the_draft_streams_live_and_matches_the_validated_answer() -> None:
    answer = "The order sync job runs nightly at 02:00 and last completed cleanly."
    turn, durable, live = await _run_turn([f"{answer}\n{_grounding()}"])

    shown = "".join(
        payload["content"] for kind, payload in live if kind == "assistant.delta"
    )
    assert shown.strip() == answer
    # The validated answer is what the caller persists and replays; the live text
    # agreeing with it is what makes the provisional display safe here.
    assert turn.grounded.content == answer
    # Draft prose never goes out on the durable channel: it is not replayable.
    assert [kind for kind, _ in durable if kind == "assistant.delta"] == []


async def test_the_draft_is_shown_before_validation_finishes() -> None:
    answer = "The order sync job runs nightly at 02:00 and last completed cleanly."
    _turn, _durable, live = await _run_turn([f"{answer}\n{_grounding()}"])

    kinds = [kind for kind, _ in live]
    phases = [
        payload.get("phase") for kind, payload in live if kind == "assistant.progress"
    ]
    # Whole point of the change: text is on screen while the validator is still
    # working, instead of arriving in one burst after it.
    assert "assistant.delta" in kinds
    if "validating_answer" in phases:
        first_delta = kinds.index("assistant.delta")
        validating = next(
            index
            for index, (kind, payload) in enumerate(live)
            if kind == "assistant.progress"
            and payload.get("phase") == "validating_answer"
        )
        assert first_delta < validating


async def test_a_rejected_draft_is_retracted_before_the_rewrite_streams() -> None:
    """The client must not append the corrected answer to the refused one."""

    refused = "The job runs nightly, according to a source I should not have cited."
    accepted = "The order sync job runs nightly at 02:00."
    turn, _durable, live = await _run_turn(
        [
            # Cites evidence no tool returned -> CORRECTABLE, so the loop asks for
            # a rewrite and the draft on screen is not the answer.
            f"{refused}\n"
            + _grounding(
                citations=[{"claim_key": "schedule", "evidence_refs": ["doc:1:2"]}]
            ),
            f"{accepted}\n{_grounding()}",
        ]
    )

    kinds = [kind for kind, _ in live]
    assert "assistant.draft_reset" in kinds
    reset_at = kinds.index("assistant.draft_reset")
    before = "".join(
        payload["content"]
        for kind, payload in live[:reset_at]
        if kind == "assistant.delta"
    )
    after = "".join(
        payload["content"]
        for kind, payload in live[reset_at:]
        if kind == "assistant.delta"
    )
    assert before.strip() == refused
    assert after.strip() == accepted
    assert turn.grounded.content == accepted


async def test_the_flag_off_restores_the_burst_after_validation() -> None:
    answer = "The order sync job runs nightly at 02:00 and last completed cleanly."
    turn, _durable, live = await _run_turn(
        [f"{answer}\n{_grounding()}"], stream_draft=False
    )

    assert [kind for kind, _ in live if kind == "assistant.delta"] == []
    assert turn.grounded.content == answer
