"""Flat per-message provenance projected from one turn's recorded observations.

``conversation_messages`` carries three flat provenance columns --
``runtime_tool_summary``, ``primary_source_layer`` and ``fallback_used`` -- that
predate the harness agent runtime. Their last writer was the legacy layered-RAG
chain (``src/generation/conversational_rag.py``, deleted in ``c7e4fab``), so on
the harness path they were never populated and diagnosing a bad answer meant
hand-querying ``harness_agent_steps`` / ``harness_agent_public_events``.

This module is the single projection that fills them again. It re-derives
nothing: every value comes from observations and the evidence summary the turn
already produced, so it cannot change answer content, tool execution, or the
manifest snapshot hash.

Safety contract: only server-authored, allowlisted scalars leave this module.
Capability ids are the same identifiers already persisted in
``harness_agent_turn_audit.capability_ids``; error codes must match a strict
lowercase token pattern; ``error_message``, tool arguments, evidence bodies and
provider URLs are never read. This mirrors the ``public_payload`` discipline in
:mod:`src.harness.agent_runs`.

Truthfulness contract: an ambiguous layer is persisted as ``None``. A wrong
provenance label is worse than a missing one, because the column exists to make
bad answers diagnosable.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.harness.capability_runtime import CapabilityObservation
from src.harness.evidence_summary import EvidenceSummary
from src.schemas import DocumentSource

logger = logging.getLogger(__name__)

#: Matches the 12-capability bound already used for the turn audit row, so both
#: projections of the same turn truncate at the same place.
_MAX_SUMMARY_ENTRIES = 12

#: ``primary_source_layer`` is ``VARCHAR(50)``, so the pattern is bounded at 50.
#: A value that would not fit is dropped, never truncated into a wrong label.
_LAYER_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,50}$")
_ERROR_CODE_RE = re.compile(r"^[a-z0-9_.:-]{1,60}$")
_CAPABILITY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

#: The layer legacy chunks and pre-partition corpora implicitly belong to. The
#: deleted legacy writer used exactly this default for a document source whose
#: ``source_layer`` payload key was absent.
_DEFAULT_DOCUMENT_LAYER = "canonical_docs"


@dataclass(frozen=True, slots=True)
class TurnProvenance:
    """The flat provenance columns for one assistant message.

    ``doc_gap`` is deliberately absent: it means "canonical docs were
    insufficient AND an internal corpus layer supplied the answer", which only
    the deleted retrieval ladder could observe. The harness runtime computes no
    equivalent signal, so the column keeps its ``false`` default rather than a
    guess.
    """

    runtime_tool_summary: list[dict[str, Any]] | None = None
    primary_source_layer: str | None = None
    fallback_used: bool = False


def build_runtime_capability_summary(
    observations: Sequence[CapabilityObservation],
) -> list[dict[str, Any]] | None:
    """Aggregate what ran this turn into a compact, user-safe record.

    One entry per ``(capability_id, status)`` pair in first-appearance order:
    repeated calls of the same capability with the same outcome collapse into a
    ``call_count`` rather than lengthening a row that may be rendered. Returns
    ``None`` when the turn executed no capability, which is the truthful value
    for a model-only answer.
    """

    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for observation in observations:
        capability_id = _safe_token(observation.capability_id, _CAPABILITY_ID_RE)
        if capability_id is None:
            continue
        status = observation.status.value
        key = (capability_id, status)
        entry = entries.get(key)
        if entry is None:
            if len(entries) >= _MAX_SUMMARY_ENTRIES:
                continue
            entry = {"capability_id": capability_id, "status": status, "call_count": 0}
            entries[key] = entry
        entry["call_count"] += 1
        evidence_count = len(
            [item for item in observation.evidence_refs if isinstance(item, dict)]
        )
        if evidence_count:
            entry["evidence_count"] = entry.get("evidence_count", 0) + evidence_count
        if _observation_truncated(observation):
            entry["truncated"] = True
        if not observation.succeeded:
            error_code = _safe_token(observation.error_code, _ERROR_CODE_RE)
            if error_code is not None:
                entry.setdefault("error_code", error_code)

    return list(entries.values()) or None


def resolve_primary_source_layer(
    document_sources: Sequence[DocumentSource],
    evidence_summary: EvidenceSummary,
) -> str | None:
    """Name the source layer that primarily supported this answer.

    Two branches, one meaning -- "where did the answer come from":

    * When the answer cites indexed document chunks, the value is that corpus
      partition (``canonical_docs``, ``technical_analysis``, ``support_maps``,
      ``scorecard``, ``evidence_slices``): the vocabulary of the ``source_layer``
      chunk payload key, and the vocabulary the deleted legacy writer used.
      The highest-scoring cited document decides it, and a source with no layer
      payload resolves to ``canonical_docs`` exactly as it did before.
    * Otherwise the value is the dominant evidence family from
      :class:`~src.harness.evidence_summary.EvidenceSummary` (``topology``,
      ``runtime``, ``documentation``, ``metadata``, ``conversation``,
      ``connector``, ``other``) -- a turn answered from the topology graph has
      no corpus layer, and null there would hide the very provenance this column
      exists to record.

    Returns ``None`` when the turn gathered no evidence, or when two families
    tie for dominance: an ambiguous turn is not labelled.
    """

    if document_sources:
        primary = max(document_sources, key=lambda source: source.score)
        return _safe_token(primary.source_layer, _LAYER_TOKEN_RE) or _DEFAULT_DOCUMENT_LAYER

    counts: dict[str, int] = {}
    for source in evidence_summary.successful_sources:
        counts[source.family] = counts.get(source.family, 0) + source.evidence_count
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return _safe_token(ranked[0][0], _LAYER_TOKEN_RE)


def project_turn_provenance(
    *,
    observations: Sequence[CapabilityObservation],
    document_sources: Sequence[DocumentSource],
    evidence_summary: EvidenceSummary,
) -> TurnProvenance:
    """Project one turn's flat provenance columns. Pure -- no I/O, no re-execution."""

    return TurnProvenance(
        runtime_tool_summary=build_runtime_capability_summary(observations),
        primary_source_layer=resolve_primary_source_layer(
            document_sources, evidence_summary
        ),
        fallback_used=bool(evidence_summary.documentation.fallback_used),
    )


def safe_project_turn_provenance(
    *,
    observations: Sequence[CapabilityObservation],
    document_sources: Sequence[DocumentSource],
    evidence_summary: EvidenceSummary,
) -> TurnProvenance:
    """Project provenance, degrading to empty provenance on any defect.

    Telemetry must never cost the user their answer, so a projection defect
    yields nulls instead of propagating. It is logged with a traceback -- this
    is a degraded path, not a swallowed error.
    """

    try:
        return project_turn_provenance(
            observations=observations,
            document_sources=document_sources,
            evidence_summary=evidence_summary,
        )
    except Exception:
        logger.error("Turn provenance projection failed; persisting nulls", exc_info=True)
        return TurnProvenance()


def _observation_truncated(observation: CapabilityObservation) -> bool:
    """Whether the capability reported that it omitted results to stay in bounds.

    Handlers report this in ``safe_details`` (topology) or in the bounded
    ``data`` envelope (connector evidence, context-limited semantic results).
    Only the boolean is read; neither container is otherwise inspected.
    """

    for container in (observation.safe_details, observation.data):
        if isinstance(container, Mapping) and container.get("truncated") is True:
            return True
    return False


def _safe_token(value: Any, pattern: re.Pattern[str]) -> str | None:
    """Return ``value`` only when it is a bare token matching ``pattern``.

    Anything else -- prose, an over-long identifier, a non-string -- is dropped
    rather than cleaned, so no provider text can reach a persisted column by
    passing through a normalizer.
    """

    if not isinstance(value, str):
        return None
    token = value.strip()
    return token if token and pattern.fullmatch(token) else None


__all__ = [
    "TurnProvenance",
    "build_runtime_capability_summary",
    "project_turn_provenance",
    "resolve_primary_source_layer",
    "safe_project_turn_provenance",
]
