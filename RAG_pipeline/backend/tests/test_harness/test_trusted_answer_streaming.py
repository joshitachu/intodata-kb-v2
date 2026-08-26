"""Streaming the trusted RAG answer, and the guards that make it safe.

What is special about this path: `answer_from_documentation` produces a
deterministically cited answer, so the turn loop adopts it verbatim and the
grounding validator is bypassed. With no post-generation transform, the streamed
text cannot diverge from the persisted text -- an equality this module asserts
rather than assumes.

The outer agent model streams too, but it does NOT get that guarantee: the
validator may rewrite or replace its answer, so that text is provisional and is
corrected by the authoritative replay. Those guards live in
`test_draft_answer_streaming.py`; the two paths must not be conflated.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.config import Settings
from src.generation.rag_chain import _generate_streaming_answer
from src.harness.trusted_answer import (
    ANSWER_DELTA_CHARS,
    AnswerDeltaSink,
    TrustedAnswerSink,
)

pytestmark = pytest.mark.asyncio


def _stream_of(pieces: list[str], *, fail_after: int | None = None, delay: float = 0.0):
    async def stream(_messages, images=None):  # noqa: ANN001, ANN202
        del images
        for index, piece in enumerate(pieces):
            if fail_after is not None and index == fail_after:
                raise RuntimeError("provider dropped the connection")
            if delay:
                await asyncio.sleep(delay)
            yield piece

    return stream


# --- Streaming mechanics ---------------------------------------------------


async def test_streamed_text_is_byte_identical_to_the_returned_answer(monkeypatch) -> None:
    pieces = [f"token-{index} " for index in range(80)]
    monkeypatch.setattr(
        "src.generation.rag_chain.generate_answer_stream", _stream_of(pieces)
    )
    published: list[str] = []

    async def on_delta(text: str) -> None:
        published.append(text)

    answer = await _generate_streaming_answer([{"role": "user", "content": "q"}], on_delta)

    assert answer == "".join(pieces)
    # This equality is the whole safety property: what was shown IS what is stored.
    assert "".join(published) == answer
    # Batched, not one event per token, or a 4,000-char answer becomes 1,000 events.
    assert len(published) < len(pieces)
    assert all(len(batch) >= ANSWER_DELTA_CHARS for batch in published[:-1])


async def test_deltas_are_published_during_generation_not_after(monkeypatch) -> None:
    pieces = ["x" * 100 for _ in range(5)]
    monkeypatch.setattr(
        "src.generation.rag_chain.generate_answer_stream",
        _stream_of(pieces, delay=0.02),
    )
    timings: list[float] = []
    started = asyncio.get_running_loop().time()

    async def on_delta(text: str) -> None:
        del text
        timings.append(asyncio.get_running_loop().time() - started)

    await _generate_streaming_answer([{"role": "user", "content": "q"}], on_delta)
    total = asyncio.get_running_loop().time() - started

    assert len(timings) >= 4
    # The first batch lands early in the generation rather than at the end: that is
    # the difference between a 37s frozen UI and text appearing as it is written.
    assert timings[0] < total / 2
    assert timings == sorted(timings)


async def test_a_failure_before_any_text_is_retried_once(monkeypatch) -> None:
    attempts = 0
    pieces = ["a" * 200]

    def build():  # noqa: ANN202
        async def stream(_messages, images=None):  # noqa: ANN001, ANN202
            nonlocal attempts
            del images
            attempts += 1
            if attempts == 1:
                raise RuntimeError("SSLV3_ALERT_BAD_RECORD_MAC")
            for piece in pieces:
                yield piece

        return stream

    monkeypatch.setattr("src.generation.rag_chain.generate_answer_stream", build())
    published: list[str] = []

    async def on_delta(text: str) -> None:
        published.append(text)

    answer = await _generate_streaming_answer([{"role": "user", "content": "q"}], on_delta)
    assert attempts == 2
    assert answer == "a" * 200
    assert "".join(published) == answer


async def test_a_failure_after_text_was_shown_is_never_retried(monkeypatch) -> None:
    """A retry would replace visible text with a differently worded attempt."""

    attempts = 0

    def build():  # noqa: ANN202
        async def stream(_messages, images=None):  # noqa: ANN001, ANN202
            nonlocal attempts
            del images
            attempts += 1
            yield "b" * 200
            raise RuntimeError("connection reset mid-answer")

        return stream

    monkeypatch.setattr("src.generation.rag_chain.generate_answer_stream", build())

    async def on_delta(text: str) -> None:
        del text

    with pytest.raises(RuntimeError, match="connection reset mid-answer"):
        await _generate_streaming_answer([{"role": "user", "content": "q"}], on_delta)
    assert attempts == 1


# --- The sink -------------------------------------------------------------


async def test_an_unbound_sink_is_inert_and_keeps_the_non_streaming_path() -> None:
    sink = AnswerDeltaSink()
    assert sink.enabled is False
    assert sink.started is False
    await sink.emit("ignored")
    assert sink.streamed == ""


async def test_a_publish_failure_never_fails_the_answer() -> None:
    async def broken(_text: str) -> None:
        raise RuntimeError("transport went away")

    sink = AnswerDeltaSink(publish=broken)
    await sink.emit("shown")
    # Recorded as shown (it was handed to the transport) but no exception escapes.
    assert sink.streamed == "shown"


async def test_the_evidence_adapter_streams_and_checks_the_answer_matches(
    monkeypatch,
) -> None:
    from src.harness import evidence

    answer_text = "The deterministic answer with citations."

    async def fake_run_rag_query(request, *, on_delta=None, **kwargs):  # noqa: ANN001, ANN003, ANN202
        del request, kwargs
        if on_delta is not None:
            await on_delta(answer_text)
        return SimpleNamespace(answer=answer_text, sources=[], latency_ms=1)

    monkeypatch.setattr(evidence, "run_rag_query", fake_run_rag_query)
    published: list[str] = []

    async def publish(text: str) -> None:
        published.append(text)

    trusted = TrustedAnswerSink()
    delta_sink = AnswerDeltaSink(publish=publish)
    result = await evidence.answer_document_question_evidence(
        qdrant=None,  # type: ignore[arg-type]
        db=None,  # type: ignore[arg-type]
        query="What does the job do?",
        user_groups=[],
        user_id=1,
        project_ids=None,
        trusted_answer_sink=trusted,
        answer_delta_sink=delta_sink,
    )
    assert result.status == "succeeded"
    assert trusted.answer is not None
    assert trusted.answer.content == answer_text
    assert delta_sink.streamed == answer_text == "".join(published)


async def test_the_evidence_adapter_does_not_stream_without_a_bound_sink(
    monkeypatch,
) -> None:
    from src.harness import evidence

    seen: dict[str, object] = {}

    async def fake_run_rag_query(request, *, on_delta=None, **kwargs):  # noqa: ANN001, ANN003, ANN202
        del request, kwargs
        seen["on_delta"] = on_delta
        return SimpleNamespace(answer="Answer.", sources=[], latency_ms=1)

    monkeypatch.setattr(evidence, "run_rag_query", fake_run_rag_query)
    await evidence.answer_document_question_evidence(
        qdrant=None,  # type: ignore[arg-type]
        db=None,  # type: ignore[arg-type]
        query="what does this job do",
        user_groups=[],
        user_id=1,
        project_ids=None,
        trusted_answer_sink=TrustedAnswerSink(),
        answer_delta_sink=AnswerDeltaSink(),
    )
    assert seen["on_delta"] is None


# --- The retraction guard -------------------------------------------------


def _effect_override_applies(
    *, grounding_failed: bool, clarification: str | None, trusted: bool
) -> tuple[bool, bool]:
    """Mirror of the effect-cohort override conditions in `_run_effect_primary_turn`.

    Kept as an explicit truth table because the real branch needs a full effect
    turn to reach; the point being pinned is the boolean itself.
    """

    middleware = SimpleNamespace(
        interaction_grounding_failed=grounding_failed,
        grounded_clarification=clarification,
        # ALWAYS None for `answer_from_documentation`: it is a read capability, so
        # the "no consequential tool completed" condition can never exclude it.
        completed_consequential_semantic_name=None,
    )
    trusted_document_answer = trusted
    applies_failure = (
        middleware.interaction_grounding_failed
        and middleware.completed_consequential_semantic_name is None
        and not trusted_document_answer
    )
    applies_clarify = (
        not applies_failure
        and middleware.grounded_clarification is not None
        and middleware.completed_consequential_semantic_name is None
        and not trusted_document_answer
    )
    return applies_failure, applies_clarify


async def test_a_trusted_answer_is_never_retracted_by_a_turn_assessment_problem() -> None:
    # Without a trusted answer both overrides still fire, exactly as before.
    assert _effect_override_applies(
        grounding_failed=True, clarification=None, trusted=False
    ) == (True, False)
    assert _effect_override_applies(
        grounding_failed=False, clarification="Which project?", trusted=False
    ) == (False, True)
    # With one, neither may replace it -- streamed text must not be retracted.
    assert _effect_override_applies(
        grounding_failed=True, clarification=None, trusted=True
    ) == (False, False)
    assert _effect_override_applies(
        grounding_failed=False, clarification="Which project?", trusted=True
    ) == (False, False)


async def test_the_effect_cohort_guards_both_overrides_in_source() -> None:
    """The guard is a two-line condition; pin that neither branch lost it."""

    import inspect

    from src.harness.agent_chat_runtime import _run_effect_primary_turn

    source = inspect.getsource(_run_effect_primary_turn)
    assert source.count("and not trusted_document_answer") == 2
    assert "trusted_document_answer = runtime.trusted_answer_sink.answer is not None" in source


# --- Deadline reconciliation ----------------------------------------------


async def test_deployed_deadlines_reconcile_without_ever_aborting_earlier() -> None:
    raw = {
        "agent_runtime_v2_hard_deadline_seconds": 120,
        "agent_runtime_v2_soft_deadline_seconds": 45,
        "agent_runtime_v2_read_tool_timeout_seconds": 90,
        "agent_runtime_v2_model_timeout_seconds": 60,
        "kimi_timeout": 300,
    }
    settings = Settings(**raw)
    # The hard deadline is the only authority and is never moved.
    assert settings.agent_runtime_v2_hard_deadline_seconds == 120
    # A finished answer is no longer discarded by a limit below the turn's own.
    assert settings.agent_runtime_v2_read_tool_timeout_seconds == 120
    # The soft gate reserves exactly one model call, instead of refusing second
    # rounds that would have finished comfortably.
    assert settings.agent_runtime_v2_soft_deadline_seconds == 60
    # A provider socket may not outlive the turn it belongs to.
    assert settings.kimi_timeout == 120

    # Every adjusted bound moved later, except the provider socket which was
    # clamped down to -- never below -- the hard deadline.
    assert settings.agent_runtime_v2_read_tool_timeout_seconds >= raw[
        "agent_runtime_v2_read_tool_timeout_seconds"
    ]
    assert settings.agent_runtime_v2_soft_deadline_seconds >= raw[
        "agent_runtime_v2_soft_deadline_seconds"
    ]
    assert settings.kimi_timeout <= raw["kimi_timeout"]
    assert settings.kimi_timeout >= settings.agent_runtime_v2_hard_deadline_seconds


async def test_already_consistent_deadlines_are_left_alone() -> None:
    settings = Settings(
        agent_runtime_v2_hard_deadline_seconds=300,
        agent_runtime_v2_soft_deadline_seconds=280,
        agent_runtime_v2_read_tool_timeout_seconds=300,
        agent_runtime_v2_model_timeout_seconds=60,
        kimi_timeout=120,
    )
    assert settings.agent_runtime_v2_soft_deadline_seconds == 280
    assert settings.agent_runtime_v2_read_tool_timeout_seconds == 300
    assert settings.kimi_timeout == 120


async def test_a_model_timeout_at_or_above_the_hard_deadline_leaves_soft_untouched() -> None:
    settings = Settings(
        agent_runtime_v2_hard_deadline_seconds=120,
        agent_runtime_v2_soft_deadline_seconds=45,
        agent_runtime_v2_model_timeout_seconds=120,
    )
    assert settings.agent_runtime_v2_soft_deadline_seconds == 45
    assert settings.agent_runtime_v2_hard_deadline_seconds == 120


async def test_the_soft_deadline_always_stays_inside_the_hard_deadline() -> None:
    for hard, soft, model in ((120, 45, 60), (300, 180, 60), (60, 10, 5), (600, 599, 1)):
        settings = Settings(
            agent_runtime_v2_hard_deadline_seconds=hard,
            agent_runtime_v2_soft_deadline_seconds=soft,
            agent_runtime_v2_model_timeout_seconds=model,
        )
        assert settings.agent_runtime_v2_soft_deadline_seconds < hard
        assert (
            settings.agent_runtime_v2_read_tool_timeout_seconds
            >= settings.agent_runtime_v2_hard_deadline_seconds
        )
        # A LayeredBudgetController must accept every reconciled combination.
        assert (
            settings.agent_runtime_v2_soft_deadline_seconds
            < settings.agent_runtime_v2_hard_deadline_seconds
        )


# --- Delta fidelity -------------------------------------------------------


async def test_answer_deltas_survive_sanitization_verbatim() -> None:
    """Chunked prose must not be whitespace-folded on its way to the client.

    Deltas are concatenated by the client. Folding each chunk collapses the
    answer's newlines into spaces and trims the space at every chunk boundary, so
    the reassembled text is neither valid Markdown nor equal to the stored answer.
    Now that the answer streams while it is generated, that folded text is what
    the user reads for the whole generation.
    """

    from src.harness.agent_runs import sanitize_public_payload
    from src.harness.readonly_runtime import public_answer_chunks

    answer = (
        "## Payment loader\n\n"
        "It reads settlement files and validates them.\n\n"
        "- enriches each record\n"
        "- routes rejects to the exception queue\n"
    )
    reassembled = "".join(
        sanitize_public_payload("assistant.delta", {"content": chunk})["content"]
        for chunk in public_answer_chunks(answer)
    )
    assert reassembled == answer
    assert "\n" in reassembled


async def test_answer_deltas_are_still_credential_redacted() -> None:
    """Exact-value handling skips folding only, never the security scrubbing."""

    from src.harness.agent_runs import sanitize_public_payload

    payload = sanitize_public_payload(
        "assistant.delta", {"content": "use api_key=super-secret-value now"}
    )
    assert "super-secret-value" not in payload["content"]
    assert "[REDACTED]" in payload["content"]


async def test_only_allowlisted_delta_fields_survive() -> None:
    from src.harness.agent_runs import sanitize_public_payload

    payload = sanitize_public_payload(
        "assistant.delta", {"content": "shown", "prompt": "hidden", "phase": "hidden"}
    )
    assert payload == {"content": "shown"}
