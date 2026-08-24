"""Per-turn progress phase reporting for long-running work.

The chat turn's status line is owned by the harness, but the work the user is
actually waiting on frequently happens several layers down: a single
``answer_from_documentation`` tool call runs the whole deterministic RAG pipeline
and can dominate the turn. Those layers must be able to say *what they are doing*
without importing the harness or having a reporter threaded through every
signature, so this leaf module holds a ``ContextVar`` sink that the harness binds
for the duration of a turn.

Layering: stdlib-only imports, like :mod:`src.turn_timings`, so ``src/generation``
and ``src/retrieval`` can report into it with no dependency on ``src/harness``.

Reporting is always safe: outside a scope :func:`report_phase` is a no-op, so code
paths that are not part of an instrumented chat turn pay nothing.

What may be reported is a PHASE NAME ONLY — never model output, a draft excerpt,
a document title, or a user's query. The harness maps the name to a
server-authored label; anything else would leak unvalidated content into a
channel that deliberately has no content field.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

#: Receives a phase name. Implemented by the harness progress reporter.
PhaseSink = Callable[[str], Awaitable[None]]

_CURRENT: contextvars.ContextVar[PhaseSink | None] = contextvars.ContextVar(
    "nexus_turn_progress", default=None
)


@contextmanager
def turn_progress_scope(sink: PhaseSink) -> Iterator[None]:
    """Bind ``sink`` as the progress reporter for the turn in scope.

    Tasks started inside the scope inherit it, so a phase reported from a nested
    tool call reaches the turn that started it.
    """

    token = _CURRENT.set(sink)
    try:
        yield
    finally:
        _CURRENT.reset(token)


async def report_phase(phase: str) -> None:
    """Report that the turn has entered ``phase``; a no-op outside a scope.

    Never raises: progress is cosmetic and must not be able to fail the work it
    is describing.
    """

    sink = _CURRENT.get()
    if sink is None:
        return
    try:
        await sink(phase)
    except Exception:
        logger.debug("Progress phase %s could not be reported", phase, exc_info=True)


__all__ = ["PhaseSink", "report_phase", "turn_progress_scope"]
