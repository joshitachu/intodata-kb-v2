"""Per-turn component latency accumulation (embedding / search / model).

Component latencies are *produced* deep inside the retrieval and model layers but
are only *meaningful* aggregated over one chat turn, at the turn's
usage-persistence site. Threading an accumulator parameter through every
retrieval signature (and through third-party agent middleware) is not possible,
so this module owns one ``ContextVar``-scoped accumulator instead.

Layering: this is a leaf module with stdlib-only imports, so both
``src/retrieval/**`` and ``src/harness/**`` can report into it without either
importing the other.

Semantics, deliberately non-overlapping so the three numbers can be compared:

- ``embedding_ms`` -- wall time inside query-embedding calls.
- ``search_ms``    -- wall time inside retrieval entry points *excluding* the
  embedding time recorded within them (vector/hybrid search plus reranking).
  Re-entrant retrieval calls are counted once, at the outermost scope.
- ``model_ms``     -- wall time inside agent model (LLM) calls.

Recording is always safe: outside a scope every ``record_*`` call is a no-op, so
code paths that are not part of an instrumented turn pay nothing and cannot fail.
"""

from __future__ import annotations

import contextvars
import functools
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")


@dataclass(slots=True)
class TurnTimings:
    """Mutable per-turn accumulator of component latencies."""

    embedding_seconds: float = 0.0
    search_seconds: float = 0.0
    model_seconds: float = 0.0
    embedding_calls: int = 0
    search_calls: int = 0
    model_calls: int = 0
    #: Re-entrancy depth of the retrieval measurement, so nested retrieval entry
    #: points (``search_layered`` -> ``search_and_rerank`` -> ``search_similar``)
    #: are attributed once instead of two or three times.
    _search_depth: int = 0

    def add_embedding(self, seconds: float) -> None:
        self.embedding_seconds += max(0.0, seconds)
        self.embedding_calls += 1

    def add_search(self, seconds: float) -> None:
        self.search_seconds += max(0.0, seconds)
        self.search_calls += 1

    def add_model(self, seconds: float) -> None:
        self.model_seconds += max(0.0, seconds)
        self.model_calls += 1

    @property
    def embedding_ms(self) -> int:
        return round(self.embedding_seconds * 1_000)

    @property
    def search_ms(self) -> int:
        return round(self.search_seconds * 1_000)

    @property
    def model_ms(self) -> int:
        return round(self.model_seconds * 1_000)

    def component_ms(self) -> dict[str, int]:
        """Project the accumulator as the persisted component-timing columns."""

        return {
            "embedding_ms": self.embedding_ms,
            "search_ms": self.search_ms,
            "llm_ms": self.model_ms,
        }


_CURRENT: contextvars.ContextVar[TurnTimings | None] = contextvars.ContextVar(
    "nexus_turn_timings", default=None
)


def current_turn_timings() -> TurnTimings | None:
    """Return the accumulator for the turn in scope, or ``None`` outside one."""

    return _CURRENT.get()


@contextmanager
def turn_timings_scope() -> Iterator[TurnTimings]:
    """Bind a fresh accumulator for one turn and yield it.

    Tasks spawned inside the scope inherit a copy of the context that still
    points at this same accumulator object, so timings recorded by nested agent
    or retrieval tasks aggregate into the turn that started them.
    """

    timings = TurnTimings()
    token = _CURRENT.set(timings)
    try:
        yield timings
    finally:
        _CURRENT.reset(token)


def record_embedding_seconds(seconds: float) -> None:
    """Attribute ``seconds`` of query-embedding time to the turn in scope."""

    timings = _CURRENT.get()
    if timings is not None:
        timings.add_embedding(seconds)


def record_model_seconds(seconds: float) -> None:
    """Attribute ``seconds`` of model (LLM) time to the turn in scope."""

    timings = _CURRENT.get()
    if timings is not None:
        timings.add_model(seconds)


@contextmanager
def measure_search() -> Iterator[None]:
    """Attribute the wrapped retrieval call's own time to the turn.

    Embedding *and* model time recorded while the block runs are subtracted, so
    the three columns never double-count the same wall time. Model time matters
    here because query expansion calls an LLM from inside retrieval: without the
    subtraction that inference would be billed to both ``search_ms`` and
    ``llm_ms``. Only the outermost scope records, so wrapping several layers of
    retrieval entry points is safe.
    """

    timings = _CURRENT.get()
    if timings is None:
        yield
        return
    outermost = timings._search_depth == 0
    timings._search_depth += 1
    started = time.perf_counter()
    embedding_before = timings.embedding_seconds
    model_before = timings.model_seconds
    try:
        yield
    finally:
        timings._search_depth -= 1
        if outermost:
            elapsed = time.perf_counter() - started
            nested = (timings.embedding_seconds - embedding_before) + (
                timings.model_seconds - model_before
            )
            timings.add_search(elapsed - nested)


def measured_retrieval(
    func: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Attribute one retrieval entry point's non-embedding time to the turn.

    Applied to public retrieval entry points rather than to individual vector
    queries: the depth guard in :func:`measure_search` means the outermost
    decorated call is the one that records, so nesting (``search_layered`` ->
    ``search_and_rerank`` -> ``search_similar_hybrid``) is attributed once and
    still includes query expansion and reranking.
    """

    @functools.wraps(func)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with measure_search():
            return await func(*args, **kwargs)

    return wrapper


@contextmanager
def measure_model() -> Iterator[None]:
    """Attribute the wrapped model call's wall time to the turn in scope."""

    started = time.perf_counter()
    try:
        yield
    finally:
        record_model_seconds(time.perf_counter() - started)


__all__ = [
    "TurnTimings",
    "current_turn_timings",
    "measure_model",
    "measure_search",
    "measured_retrieval",
    "record_embedding_seconds",
    "record_model_seconds",
    "turn_timings_scope",
]
