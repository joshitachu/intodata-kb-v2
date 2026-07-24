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

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from src.schemas import DocumentSource


class TrustedRagAnswer(BaseModel):
    """A deterministic answer produced by the existing RAG query pipeline."""

    content: str
    document_sources: list[DocumentSource] = Field(default_factory=list)


@dataclass(slots=True)
class TrustedAnswerSink:
    """Mutable one-shot slot set by the answer adapter, read by the turn loop."""

    answer: TrustedRagAnswer | None = field(default=None)


__all__ = ["TrustedAnswerSink", "TrustedRagAnswer"]
