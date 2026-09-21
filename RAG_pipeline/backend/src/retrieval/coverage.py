"""What retrieval actually covered, and how much of it reached the answer.

Retrieval loses material at four points, and every one of them was silent:

* **Ranking.** Semantic top-k returns the chunks that scored best, not the chunks
  that answer the question. A question about nine named interfaces that retrieves
  five produces a confident, well-formed answer about five.
* **The reference cap.** ``merge_sources_by_document`` keeps at most
  ``settings.max_result_documents`` documents. A document past the cap still has
  its text in the prompt but has no ``[N]`` to be cited by, so the model either
  ignores it or attributes it to someone else's number.
* **The context budget.** ``prompt_builder._build_context`` truncates or drops
  sources that do not fit ``MAX_CONTEXT_TOKENS``. It logs the count and moves on.
* **Named entities.** The corpus knows, by name, every job/component/table in a
  project (``entity_aliases``). A question can name one and still get back no
  passage that mentions it.

None of these is a bug on its own -- each is a bounded resource doing its job.
The bug is that nothing told the model, so an answer about the surviving subset
was presented as an answer to the whole question. The only signal that ever
existed was ``NO_RESULTS_PROMPT``, which fires when retrieval returns *literally
zero*: all-or-nothing, with no partial state in between.

This module is the measurement. ``prompt_builder.coverage_instruction`` is the
disclosure. Nothing here scores, guesses or weights: every field is a count of
something the pipeline observed, and a caller that cannot observe one leaves it
at its neutral value rather than estimating it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# The boundary/casefold rule is imported rather than re-expressed. Two
# independent ideas of "does this query name this entity" is exactly how a
# coverage report starts disagreeing with the retrieval it is reporting on --
# claiming an entity is missing that the matcher fetched, or vice versa. Same
# reasoning as `grounding.py` importing the leak regexes from `synthesis`.
from src.retrieval.entity_matcher import (
    EntityMatch,
    _contains_on_boundary,
    _normalize,
)
from src.schemas import Source

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextFit:
    """How many offered sources survived the prompt's token budget.

    ``truncated`` sources ARE in the prompt with their tail cut; ``dropped``
    ones are not in the prompt at all. The distinction matters to the reader of
    the disclosure: a truncated table may still be counted from (badly), while a
    dropped one cannot be known about.
    """

    offered: int = 0
    used: int = 0
    truncated: int = 0

    @property
    def dropped(self) -> int:
        return max(0, self.offered - self.used)

    def merged_with(self, other: ContextFit) -> ContextFit:
        """Combine two sections of one prompt (role-aware context builds several)."""

        return ContextFit(
            offered=self.offered + other.offered,
            used=self.used + other.used,
            truncated=self.truncated + other.truncated,
        )


@dataclass(frozen=True, slots=True)
class EntityCoverage:
    """Named entities the question asked about, and which ones a passage carries.

    ``index_available`` is False for a project that was never alias-synced. That
    is not "the question names nothing known" -- it is "we cannot tell", and the
    two must not render the same way: reporting zero missing entities for an
    unsynced project would assert coverage nobody measured.
    """

    named: tuple[str, ...] = ()
    covered: tuple[str, ...] = ()
    index_available: bool = False

    @property
    def missing(self) -> tuple[str, ...]:
        covered = set(self.covered)
        return tuple(name for name in self.named if name not in covered)


@dataclass(frozen=True, slots=True)
class RetrievalCoverage:
    """One turn's answer to "what was in scope, and what reached the prompt?"."""

    retrieved_chunks: int = 0
    retrieved_documents: int = 0
    referenced_documents: int = 0
    entities: EntityCoverage = field(default_factory=EntityCoverage)
    context: ContextFit | None = None

    @property
    def unreferenced_documents(self) -> int:
        """Documents in the prompt that the model has no ``[N]`` for."""

        return max(0, self.retrieved_documents - self.referenced_documents)

    @property
    def has_gap(self) -> bool:
        """True when something measurable was left out of the answer's evidence.

        Deliberately inclusive. Over-disclosing costs a sentence the reader can
        ignore; under-disclosing is the failure this module exists for, and the
        rendered instruction is conditional ("if the question asks for a complete
        set...") so a weak gap does not force a caveat onto a lookup.
        """

        fit = self.context
        return bool(
            self.entities.missing
            or self.unreferenced_documents
            or (fit is not None and (fit.dropped or fit.truncated))
        )

    def log_line(self) -> str:
        """A single structured line for the query log. No prose, no model text."""

        fit = self.context or ContextFit()
        return (
            f"coverage chunks={self.retrieved_chunks} docs={self.retrieved_documents} "
            f"referenced={self.referenced_documents} "
            f"context_used={fit.used}/{fit.offered} truncated={fit.truncated} "
            f"entities_named={len(self.entities.named)} "
            f"entities_missing={len(self.entities.missing)} "
            f"entity_index={'yes' if self.entities.index_available else 'no'}"
        )


def _carries_entity(source: Source, canonical: str, unit_name: str | None) -> bool:
    """Whether one retrieved chunk actually covers one named entity.

    Two independent ways to be covered, because the corpus records the fact in
    two places and either one is proof:

    * the chunk's ``unit`` payload names the entity or its owning unit -- the
      structural claim the ingester made about the chunk; or
    * the entity's canonical name appears in the chunk text on a word boundary.

    Boundary-aware, not substring: ``tDBOutput_1`` must not count as coverage of
    ``tDBOutput_11``, which is a different component.
    """

    unit = _normalize(source.unit or "")
    if unit and unit in {canonical, _normalize(unit_name or "")}:
        return True
    return _contains_on_boundary(_normalize(source.text or ""), canonical)


def entity_coverage(
    matches: Sequence[EntityMatch],
    sources: Iterable[Source],
    *,
    index_available: bool,
) -> EntityCoverage:
    """Which of the question's named entities a retrieved chunk actually carries.

    ``matches`` is what ``match_query_entities`` detected in the question, so
    every name here is one the corpus knows AND the user asked about -- not a
    heuristic guess at what an identifier looks like. An entity in ``named`` and
    not in ``covered`` is the strongest partial-coverage signal available: the
    user named a thing, the project has it, and no passage in front of the model
    mentions it.
    """

    ordered: list[str] = []
    seen: set[str] = set()
    units: dict[str, str | None] = {}
    for match in matches:
        canonical = _normalize(match.canonical_name)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        ordered.append(match.canonical_name)
        units[match.canonical_name] = match.unit_name

    if not ordered:
        return EntityCoverage(index_available=index_available)

    materialized = list(sources)
    covered = tuple(
        name
        for name in ordered
        if any(
            _carries_entity(source, _normalize(name), units.get(name))
            for source in materialized
        )
    )
    return EntityCoverage(
        named=tuple(ordered),
        covered=covered,
        index_available=index_available,
    )


def build_retrieval_coverage(
    *,
    sources: Sequence[Source],
    auxiliary_sources: Sequence[Source] = (),
    referenced_documents: int = 0,
    entities: EntityCoverage | None = None,
    context: ContextFit | None = None,
) -> RetrievalCoverage:
    """Assemble one turn's coverage from what the chain already has in hand.

    Counts documents across BOTH role partitions: the context the model reads is
    the union of them, so a coverage number computed over source documents alone
    would disagree with the reference list it is compared against.
    """

    chunks = [*sources, *auxiliary_sources]
    documents = {chunk.document_id for chunk in chunks}
    return RetrievalCoverage(
        retrieved_chunks=len(chunks),
        retrieved_documents=len(documents),
        referenced_documents=referenced_documents,
        entities=entities or EntityCoverage(),
        context=context,
    )


__all__ = [
    "ContextFit",
    "EntityCoverage",
    "RetrievalCoverage",
    "build_retrieval_coverage",
    "entity_coverage",
]
