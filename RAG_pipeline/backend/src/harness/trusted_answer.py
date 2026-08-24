"""Neutral sink carrying a deterministic RAG answer into the read-only runtime.

This module intentionally imports nothing from ``src.harness`` so it can be
imported by ``evidence.py``, ``readonly_runtime.py``, and
``agent_chat_runtime.py`` without creating an import cycle. It exists solely
to let the ``rag.docs.answer_question`` capability adapter hand its
deterministically generated answer (content plus document sources) directly
to the read-only turn loop, bypassing the self-reported grounding block for
that one turn.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from src.schemas import DocumentSource

logger = logging.getLogger(__name__)

#: Transport-sized delta batch. Matches ``public_answer_chunks``' slice width, so
#: the live stream and the authoritative post-persistence replay have the same
#: granularity and event volume stays bounded on a long answer.
ANSWER_DELTA_CHARS = 96


class TrustedRagAnswer(BaseModel):
    """A deterministic answer produced by the existing RAG query pipeline."""

    content: str
    document_sources: list[DocumentSource] = Field(default_factory=list)


@dataclass(slots=True)
class TrustedAnswerSink:
    """Mutable one-shot slot set by the answer adapter, read by the turn loop."""

    answer: TrustedRagAnswer | None = field(default=None)
    documentation_provider_trace: dict | None = field(default=None)


@dataclass(slots=True)
class AnswerDeltaSink:
    """Live delta channel for the ONE path where streaming answer text is safe.

    Why a mutable sink rather than a parameter: the request runtime (and its
    capability adapters) is assembled *before* the public event callback exists,
    which is the same ordering that made ``TrustedAnswerSink`` a sink. The harness
    binds :attr:`publish` once the callback is available; until then the sink is
    inert and generation simply does not stream.

    SAFETY -- this channel may only be fed by ``answer_from_documentation``. That
    capability's answer is produced by the deterministic RAG pipeline with real
    citations, so the turn loop adopts it verbatim and the grounding validator is
    bypassed: there is no post-generation transform, which is precisely why the
    streamed text is byte-identical to what gets persisted. On every other path
    the validator can rewrite, downgrade or replace the answer, so streaming there
    would risk showing text that validation later rejects.

    Deltas published here are provisional: they are emitted ``replayable=False``,
    and the authoritative replayable emission after persistence remains the record.
    """

    publish: Callable[[str], Awaitable[None]] | None = field(default=None)
    #: Exact concatenation of everything published, for equality assertions
    #: against the persisted answer.
    streamed: str = field(default="")

    @property
    def enabled(self) -> bool:
        return self.publish is not None

    @property
    def started(self) -> bool:
        """True once any text has been shown; a retry after this would diverge."""

        return bool(self.streamed)

    async def emit(self, text: str) -> None:
        """Publish one delta. Never raises: a display failure must not fail the answer."""

        if not text or self.publish is None:
            return
        self.streamed += text
        try:
            await self.publish(text)
        except Exception:
            logger.debug("Live answer delta was not published", exc_info=True)


__all__ = [
    "ANSWER_DELTA_CHARS",
    "AnswerDeltaSink",
    "TrustedAnswerSink",
    "TrustedRagAnswer",
]
