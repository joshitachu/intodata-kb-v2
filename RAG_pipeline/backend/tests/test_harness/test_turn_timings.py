"""Component-latency accumulation semantics for one chat turn."""

from __future__ import annotations

import asyncio

import pytest

from src.turn_timings import (
    TurnTimings,
    current_turn_timings,
    measure_model,
    measure_search,
    measured_retrieval,
    record_embedding_seconds,
    record_model_seconds,
    turn_timings_scope,
)

pytestmark = pytest.mark.asyncio


async def test_recording_outside_a_scope_is_a_no_op() -> None:
    assert current_turn_timings() is None
    record_embedding_seconds(1.0)
    record_model_seconds(1.0)
    with measure_search():
        pass
    assert current_turn_timings() is None


async def test_search_time_excludes_the_embedding_time_inside_it() -> None:
    with turn_timings_scope() as timings:
        with measure_search():
            record_embedding_seconds(0.30)
            await asyncio.sleep(0.02)
    assert timings.embedding_ms == 300
    # The 0.30s of embedding is not also billed to search, so the two columns can
    # be added without double counting the same wall time.
    assert timings.search_ms < 300
    assert timings.search_calls == 1
    assert timings.embedding_calls == 1


async def test_nested_retrieval_entry_points_are_counted_once() -> None:
    @measured_retrieval
    async def inner() -> str:
        await asyncio.sleep(0.01)
        return "inner"

    @measured_retrieval
    async def outer() -> str:
        await inner()
        await inner()
        return "outer"

    with turn_timings_scope() as timings:
        assert await outer() == "outer"
    assert timings.search_calls == 1


async def test_model_time_accumulates_across_calls_and_survives_failure() -> None:
    with turn_timings_scope() as timings:
        with measure_model():
            await asyncio.sleep(0.01)
        with pytest.raises(RuntimeError):
            with measure_model():
                await asyncio.sleep(0.01)
                raise RuntimeError("provider blew up")
    assert timings.model_calls == 2
    # A slow failure must not look instant.
    assert timings.model_ms >= 15


async def test_component_projection_matches_the_persisted_column_names() -> None:
    timings = TurnTimings(embedding_seconds=0.1, search_seconds=0.2, model_seconds=3.0)
    assert timings.component_ms() == {
        "embedding_ms": 100,
        "search_ms": 200,
        "llm_ms": 3_000,
    }


async def test_tasks_started_inside_a_scope_report_into_the_same_turn() -> None:
    async def worker() -> None:
        record_model_seconds(0.5)

    with turn_timings_scope() as timings:
        await asyncio.gather(asyncio.create_task(worker()), asyncio.create_task(worker()))
    assert timings.model_calls == 2
    assert timings.model_ms == 1_000


async def test_scopes_do_not_leak_into_each_other() -> None:
    with turn_timings_scope() as first:
        record_model_seconds(1.0)
        with turn_timings_scope() as second:
            record_model_seconds(2.0)
        record_model_seconds(1.0)
    assert first.model_ms == 2_000
    assert second.model_ms == 2_000
    assert current_turn_timings() is None
