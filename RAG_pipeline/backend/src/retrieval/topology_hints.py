"""Server-owned topology hints for documentation retrieval.

Topology and documentation are complementary evidence families.  This module
uses the published topology only as an *addressing aid*: exact entity names,
aliases, implementation names, jobs, and source roots can add one bounded
semantic query to the normal RAG search.  They never become Qdrant filters and
never contribute or modify project scope.

The resolver deliberately requires both an exact one-project scope and the
authenticated request's access token.  Browser ``page_scope`` and model-authored
identifiers are not authority; MCP rechecks the token when serving each artifact.
Failures degrade to no hints, leaving the established documentation query intact.
"""

from __future__ import annotations

import asyncio
import logging
import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.topology.graph import TopologyNode, build_design_index, build_graph
from src.topology.provider import TopologyProvider, default_provider

logger = logging.getLogger(__name__)

HINT_CONTRACT_VERSION = "1"
MAX_MATCHED_ENTITIES = 6
MAX_ALIASES = 8
MAX_ARTIFACT_NAMES = 8
MAX_JOB_NAMES = 8
MAX_SOURCE_ROOTS = 4
MAX_RETRIEVAL_TERMS = 20
MAX_TERM_CHARS = 160
MAX_ENRICHED_QUERY_CHARS = 1_200

# These labels recur across scanner partitions or describe technology rather
# than a unique business identity in the WerkOrder fixture.  They may still
# improve recall, but can never be treated as exact or narrowing hints.
GENERIC_TOPOLOGY_LABELS = frozenset({"common", "log", "talend", "quintiq"})

_SAFE_TERM_RE = re.compile(r"[^A-Za-z0-9À-ÖØ-öø-ÿ_.:/+ -]+")
_SPACE_RE = re.compile(r"\s+")


class TopologyHintStrength(StrEnum):
    """How confidently the query names topology identities."""

    EXACT = "exact"
    WEAK = "weak"
    AMBIGUOUS = "ambiguous"


class TopologyRetrievalHints(BaseModel):
    """Bounded internal contract passed from topology resolution to RAG.

    ``project_id`` records where MCP resolved the identities.  Consumers must
    not copy it into retrieval scope: the already-authorized ``project_ids``
    argument remains the only scope authority.  ``exclusive_filter_allowed`` is
    a literal false release gate until scanner identity quality is certified.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["1"] = HINT_CONTRACT_VERSION
    project_id: str = Field(min_length=1, max_length=100)
    provenance: Literal["published_topology"] = "published_topology"
    strength: TopologyHintStrength
    ambiguous: bool = False
    matched_entity_ids: tuple[str, ...] = ()
    matched_entity_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    artifact_names: tuple[str, ...] = ()
    job_names: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ()
    generic_labels: tuple[str, ...] = ()
    retrieval_terms: tuple[str, ...] = ()
    exclusive_filter_allowed: Literal[False] = False

    def enriched_query(self, query: str) -> str:
        """Return a bounded semantic expansion without changing the question."""

        if not self.retrieval_terms:
            return query
        suffix = " Related project identifiers: " + "; ".join(self.retrieval_terms) + "."
        available = max(0, MAX_ENRICHED_QUERY_CHARS - len(query))
        return query + suffix[:available]


async def resolve_topology_retrieval_hints(
    *,
    query: str,
    project_ids: list[str] | None,
    access_token: str | None,
    provider: TopologyProvider | None = None,
) -> TopologyRetrievalHints | None:
    """Resolve query identities inside one exact authenticated project.

    ``None`` means "run normal RAG".  An unrestricted/multi-project scope, a
    missing request token, an unreadable artifact, or no named identity all use
    that safe degradation.  In particular, this function never chooses a
    project from a user's words and never returns a new project identifier.
    """

    if len(project_ids or ()) != 1 or not access_token or not query.strip():
        return None
    project_id = project_ids[0]
    # Reuse the topology package's process-wide provider so normal chat turns
    # share the same fingerprint/TTL cache as explicit topology tool calls.
    topology_provider = provider or default_provider()

    landscape_result, design_result = await asyncio.gather(
        topology_provider.get_landscape(
            project_id=project_id,
            access_token=access_token,
        ),
        topology_provider.get_project_graph(
            project_id=project_id,
            access_token=access_token,
        ),
        return_exceptions=True,
    )
    if isinstance(landscape_result, BaseException):
        logger.info(
            "Topology retrieval hints unavailable for project %s (%s)",
            project_id,
            landscape_result.__class__.__name__,
        )
        return None

    graph = build_graph(landscape_result.landscape)
    matched = [node for node in graph.nodes if _node_is_mentioned(query, node)]
    if not matched:
        return None

    ambiguous = len(matched) > MAX_MATCHED_ENTITIES or _has_duplicate_identity(matched)
    matched = sorted(matched, key=lambda node: (node.name.casefold(), node.id))[
        :MAX_MATCHED_ENTITIES
    ]
    entity_ids = _unique((node.id for node in matched), limit=MAX_MATCHED_ENTITIES)
    entity_names = _unique((node.name for node in matched), limit=MAX_MATCHED_ENTITIES)
    aliases = _unique(
        (alias for node in matched for alias in node.aliases),
        limit=MAX_ALIASES,
    )
    generic = _unique(
        (
            name
            for name in (*entity_names, *aliases)
            if _normalized(name) in GENERIC_TOPOLOGY_LABELS
        ),
        limit=MAX_ALIASES,
    )

    matched_ids = set(entity_ids)
    pair_edges = [
        edge
        for edge in graph.edges
        if edge.source_id in matched_ids and edge.target_id in matched_ids
    ]
    artifact_names = _unique(
        (
            value
            for edge in pair_edges
            for value in (*edge.integrations, *edge.scanner_provenance)
            if not value.startswith("artifact:")
        ),
        limit=MAX_ARTIFACT_NAMES,
    )

    job_names: tuple[str, ...] = ()
    source_roots = _unique(
        (root for node in matched for root in node.source_roots),
        limit=MAX_SOURCE_ROOTS,
    )
    if not isinstance(design_result, BaseException):
        design = build_design_index(design_result.graph)
        directly_named = [job for job in design.jobs if _mentioned(query, job.name)]
        pair_tokens = {
            _normalized(name)
            for name in entity_names
            if _normalized(name) not in GENERIC_TOPOLOGY_LABELS
        }
        # When the question names two systems, implementation jobs containing
        # both names are a useful bridge to a dedicated generated-doc set.  With
        # one broad system (for example Maximo) this would collect dozens of
        # unrelated jobs, so pair expansion is intentionally disabled.
        if len(entity_names) >= 2:
            pair_tokens = {_normalized(name) for name in entity_names}
            related = [
                job
                for job in design.jobs
                if pair_tokens and all(token in _normalized(job.name) for token in pair_tokens)
            ]
        else:
            related = []
        selected_jobs = _unique_jobs([*directly_named, *related], limit=MAX_JOB_NAMES)
        job_names = tuple(job.name for job in selected_jobs)
        source_roots = _unique(
            (*source_roots, *(job.source_root for job in selected_jobs)),
            limit=MAX_SOURCE_ROOTS,
        )

    strength = (
        TopologyHintStrength.AMBIGUOUS
        if ambiguous
        else TopologyHintStrength.WEAK
        if generic and len(generic) == len(entity_names)
        else TopologyHintStrength.EXACT
    )
    terms = _unique(
        (
            *entity_names,
            *aliases,
            *artifact_names,
            *job_names,
            *source_roots,
        ),
        limit=MAX_RETRIEVAL_TERMS,
    )
    if not terms:
        return None
    return TopologyRetrievalHints(
        project_id=project_id,
        strength=strength,
        ambiguous=ambiguous,
        matched_entity_ids=entity_ids,
        matched_entity_names=entity_names,
        aliases=aliases,
        artifact_names=artifact_names,
        job_names=job_names,
        source_roots=source_roots,
        generic_labels=generic,
        retrieval_terms=terms,
    )


def _node_is_mentioned(query: str, node: TopologyNode) -> bool:
    return any(
        _mentioned(query, value)
        for value in (node.id, node.name, *node.aliases)
        if value
    )


def _mentioned(query: str, value: str) -> bool:
    needle = _normalized(value)
    haystack = _normalized(query)
    return bool(needle) and f" {needle} " in f" {haystack} "


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def _has_duplicate_identity(nodes: list[TopologyNode]) -> bool:
    labels = [_normalized(node.name) for node in nodes]
    return len(labels) != len(set(labels))


def _safe_term(value: str) -> str:
    return _SPACE_RE.sub(" ", _SAFE_TERM_RE.sub("", value)).strip()[:MAX_TERM_CHARS]


def _unique(values, *, limit: int) -> tuple[str, ...]:  # noqa: ANN001
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        safe = _safe_term(value)
        key = safe.casefold()
        if not safe or key in seen:
            continue
        seen.add(key)
        result.append(safe)
        if len(result) >= limit:
            break
    return tuple(result)


def _unique_jobs(jobs, *, limit: int):  # noqa: ANN001, ANN202
    result = []
    seen: set[tuple[str, str]] = set()
    for job in jobs:
        key = (str(job.name or "").casefold(), str(job.source_root or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(job)
        if len(result) >= limit:
            break
    return result


__all__ = [
    "GENERIC_TOPOLOGY_LABELS",
    "HINT_CONTRACT_VERSION",
    "TopologyHintStrength",
    "TopologyRetrievalHints",
    "resolve_topology_retrieval_hints",
]
