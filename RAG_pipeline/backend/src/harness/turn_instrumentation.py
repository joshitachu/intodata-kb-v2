"""Always-on latency instrumentation for the standard agent runtime.

The V2 chat cohorts spend almost all of their wall time inside model calls, but
that time was previously only observable as the turn total. This middleware
attributes it, per turn, to :mod:`src.turn_timings` so the usage-persistence path
can store a real ``llm_ms`` alongside the retrieval components.

It is deliberately separate from ``LayeredModelBudgetMiddleware``: budgets are a
canary-gated control, whereas timing must be recorded on every turn regardless of
which flags are on. It has no enforcement behaviour, adds no graph node, and
never suppresses an exception.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from langchain.agents.middleware import AgentMiddleware

from src.turn_timings import record_model_seconds

_Request = TypeVar("_Request")
_Response = TypeVar("_Response")


class ModelLatencyMiddleware(AgentMiddleware):
    """Record every model call's wall time against the turn in scope."""

    async def awrap_model_call(
        self,
        request: _Request,
        handler: Callable[[_Request], Awaitable[_Response]],
    ) -> _Response:
        started = time.perf_counter()
        try:
            return await handler(request)
        finally:
            # Recorded in ``finally`` so a timed-out or failed model call still
            # accounts for the time it consumed; a lost measurement would make a
            # slow failure look instant.
            record_model_seconds(time.perf_counter() - started)


__all__ = ["ModelLatencyMiddleware"]
