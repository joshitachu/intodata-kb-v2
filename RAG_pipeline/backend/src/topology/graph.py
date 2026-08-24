"""Projection of one landscape artifact into a navigable node/edge index.

The canvas renders exactly one of two graphs (`frontend/lib/landscapeTransform.ts`,
`landscapeToFlow`): the business overlay when it is published, validated and
current, otherwise the raw scanner graph. This module picks the same graph by
the same rule, which is what keeps the assistant and the diagram from disagreeing
about what exists — and what keeps a chip from navigating to an id the active
canvas has no node for.

Provenance is carried, not flattened. A scanner entity is named by the scan; an
overlay entity is named by the documentation agent and carries the artifact's own
`status` / `confidence` claim strength. Tool output states which one it is.

Relationships are addressable, not only traversable. The canvas lets a user
select a CONNECTION, and the page scope it then hands the assistant carries an
edge key rather than a node id — so `resolve_any` covers edges as well as nodes,
and reports both kinds side by side instead of ranking one over the other.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any

from src.topology.models import (
    BusinessDataStream,
    BusinessSystem,
    BusinessTopology,
    GraphComponentFact,
    GraphManifest,
    JobGraph,
    LandscapeApplication,
    LandscapeEdge,
    LandscapeSystem,
    NavigationIntent,
    ProjectGraph,
    ProjectLandscape,
    TopologyDirection,
    TopologyEvidence,
    VisualRole,
)

#: The frontend joins the parts of an edge key with US (unit separator); see
#: `landscapeEdgeKey`. A key built with any other separator resolves to nothing.
EDGE_KEY_SEPARATOR = "\x1f"


class TopologyView(str, Enum):
    """Which graph the canvas is currently rendering."""

    BUSINESS = "business"
    SCANNER = "scanner"


class NodeEntity(str, Enum):
    SYSTEM = "system"
    APPLICATION = "application"


class Naming(str, Enum):
    SCANNER = "scanner"
    BUSINESS_OVERLAY = "business_overlay"


class MatchTier(IntEnum):
    """Resolution strength, best first once negated in a sort key."""

    ID = 5
    EXACT_NAME = 4
    ALIAS = 3
    PREFIX = 2
    SUBSTRING = 1


_MATCH_LABELS = {
    MatchTier.ID: "id",
    MatchTier.EXACT_NAME: "exact_name",
    MatchTier.ALIAS: "alias",
    MatchTier.PREFIX: "name_prefix",
    MatchTier.SUBSTRING: "name_substring",
}


@dataclass(frozen=True)
class NodeProvenance:
    """Where this node's identity and naming came from."""

    naming: Naming
    claim_status: str
    confidence: float | None
    evidence_ids: tuple[str, ...]
    scanner_signals: tuple[str, ...]

    def as_content(self, *, max_items: int) -> dict[str, Any]:
        return {
            "naming": self.naming.value,
            "claim_status": self.claim_status,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids[:max_items]),
            "scanner_signals": list(self.scanner_signals[:max_items]),
        }


@dataclass(frozen=True)
class TopologyNode:
    id: str
    name: str
    kind: str
    entity: NodeEntity
    description: str
    role: str
    aliases: tuple[str, ...]
    source_roots: tuple[str, ...]
    artifact_count: int | None
    provenance: NodeProvenance


@dataclass(frozen=True)
class TopologyEdgeFact:
    """One relationship in the active graph, keyed as the canvas keys it."""

    key: str
    source_id: str
    target_id: str
    name: str
    dependency_type: str
    mechanism: str
    reliability: str
    confidence: str
    evidence_count: int
    naming: Naming
    claim_status: str
    business_objects: tuple[str, ...]
    protocols: tuple[str, ...]
    integrations: tuple[str, ...]
    scanner_provenance: tuple[str, ...]


@dataclass(frozen=True)
class NodeMatch:
    node: TopologyNode
    tier: MatchTier

    @property
    def match(self) -> str:
        return _MATCH_LABELS[self.tier]


@dataclass(frozen=True)
class EdgeMatch:
    edge: TopologyEdgeFact
    tier: MatchTier

    @property
    def match(self) -> str:
        return _MATCH_LABELS[self.tier]


@dataclass(frozen=True)
class EntityResolution:
    """What one string resolved to, kept split by kind rather than ranked.

    A user can select a CONNECTION on the canvas, and the page scope then
    carries an edge key. Resolution therefore has to cover edges as well as
    nodes — but the two are reported side by side, never merged into one ranked
    list. Ranking them would let a node match hide an edge match for the same
    string, and silently answering about a different entity than the one the
    user selected is the exact defect this type exists to prevent.

    A call is only answerable without asking when the whole resolution is one
    entity of one kind, which is what `only_node` / `only_edge` express.
    """

    nodes: list[NodeMatch]
    edges: list[EdgeMatch]

    @property
    def total(self) -> int:
        return len(self.nodes) + len(self.edges)

    @property
    def only_node(self) -> NodeMatch | None:
        return self.nodes[0] if len(self.nodes) == 1 and not self.edges else None

    @property
    def only_edge(self) -> EdgeMatch | None:
        return self.edges[0] if len(self.edges) == 1 and not self.nodes else None


@dataclass(frozen=True)
class TraversalStep:
    node: TopologyNode
    hop: int
    direction: TopologyDirection


@dataclass
class TopologyGraph:
    """Active-view index over one landscape artifact."""

    view: TopologyView
    nodes: list[TopologyNode]
    edges: list[TopologyEdgeFact]
    _nodes_by_id: dict[str, TopologyNode] = field(init=False, default_factory=dict)
    _outgoing: dict[str, list[TopologyEdgeFact]] = field(init=False, default_factory=dict)
    _incoming: dict[str, list[TopologyEdgeFact]] = field(init=False, default_factory=dict)
    _edges_by_key: dict[str, TopologyEdgeFact] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._nodes_by_id = {node.id: node for node in self.nodes}
        for edge in self.edges:
            self._outgoing.setdefault(edge.source_id, []).append(edge)
            self._incoming.setdefault(edge.target_id, []).append(edge)
            # Casefolded, and first-wins: an edge key is an identifier, so a
            # later duplicate is a republished copy of the same relationship and
            # must not shadow the one already indexed.
            self._edges_by_key.setdefault(edge.key.casefold(), edge)

    def node(self, node_id: str) -> TopologyNode | None:
        return self._nodes_by_id.get(node_id)

    def edge(self, key: str) -> TopologyEdgeFact | None:
        return self._edges_by_key.get(key.strip().casefold())

    def label(self, node_id: str) -> str:
        node = self._nodes_by_id.get(node_id)
        return node.name if node is not None and node.name else node_id

    def degree(self, node_id: str) -> int:
        return len(self._outgoing.get(node_id, ())) + len(self._incoming.get(node_id, ()))

    def adjacent(self, node_id: str, direction: TopologyDirection) -> list[TopologyEdgeFact]:
        """Edges touching a node.

        Direction is graph orientation (source -> target), not the artifact's
        `direction` attribute: that attribute describes the mechanism ("both"
        for a read/write database link), not who depends on whom.
        """
        if direction is TopologyDirection.UPSTREAM:
            return list(self._incoming.get(node_id, ()))
        if direction is TopologyDirection.DOWNSTREAM:
            return list(self._outgoing.get(node_id, ()))
        return [*self._incoming.get(node_id, ()), *self._outgoing.get(node_id, ())]

    def search(self, query: str, *, limit: int) -> tuple[list[NodeMatch], bool]:
        """Ranked matches across every tier — a `find` must not hide near misses."""
        needle = query.strip().casefold()
        matches = [
            NodeMatch(node=node, tier=tier)
            for node in self.nodes
            if (tier := _match_tier(node, needle)) is not None
        ]
        matches.sort(key=lambda item: (-item.tier, item.node.name.casefold(), item.node.id))
        return matches[:limit], len(matches) > limit

    def resolve(self, name_or_id: str) -> list[NodeMatch]:
        """Best-tier matches only.

        Returns every match in that tier: two systems can legitimately share a
        name, and picking one of them silently would attribute one system's
        dependencies to the other.
        """
        needle = name_or_id.strip().casefold()
        tiered: dict[MatchTier, list[TopologyNode]] = {}
        for node in self.nodes:
            tier = _match_tier(node, needle)
            if tier is not None:
                tiered.setdefault(tier, []).append(node)
        for tier in sorted(tiered, reverse=True):
            nodes = sorted(tiered[tier], key=lambda node: (node.name.casefold(), node.id))
            return [NodeMatch(node=node, tier=tier) for node in nodes]
        return []

    def resolve_edge(self, name_or_id: str) -> list[EdgeMatch]:
        """Match a relationship by canvas edge key, bare stream id, or edge name.

        The client sends BOTH key forms, verified in real page-scope payloads:
        the prefixed `business-stream<US><stream id>` that
        `business_stream_edge_key` builds, and the bare `<stream id>` on its own
        (`ds-enterpriseservicebus-rhm--smartflow-message`). Accepting only one of
        them is why a connection selected on the canvas used to resolve to
        nothing.

        Deliberately no prefix or substring tier for edges. A scanner edge's
        `name` is a generated `"<dependency_type> via <mechanism>"` string shared
        by many edges, so substring matching on it would manufacture ambiguity
        rather than resolve anything — and every form the client actually sends
        is a full key or a full name. Exact name matches that hit several edges
        are returned as several candidates, which is a real property of the
        estate and not a defect.
        """
        raw = name_or_id.strip()
        if not raw:
            return []
        for key in (raw, business_stream_edge_key(raw)):
            found = self.edge(key)
            if found is not None:
                return [EdgeMatch(edge=found, tier=MatchTier.ID)]
        needle = raw.casefold()
        named = sorted(
            (edge for edge in self.edges if edge.name.casefold() == needle),
            key=lambda edge: (edge.name.casefold(), edge.key),
        )
        return [EdgeMatch(edge=edge, tier=MatchTier.EXACT_NAME) for edge in named]

    def resolve_any(self, name_or_id: str) -> EntityResolution:
        """Node and edge matches for one string, each at its own best tier.

        Both halves are always computed and both are always reported. See
        `EntityResolution` for why they are not merged into one ranked list.
        """
        return EntityResolution(nodes=self.resolve(name_or_id), edges=self.resolve_edge(name_or_id))

    def traverse(
        self,
        root: TopologyNode,
        *,
        direction: TopologyDirection,
        hops: int,
        max_nodes: int,
        max_edges: int,
    ) -> tuple[list[TraversalStep], list[tuple[TopologyEdgeFact, int]], bool]:
        """Breadth-first walk bounded by hops and by both output caps."""
        steps: list[TraversalStep] = []
        edges: list[tuple[TopologyEdgeFact, int]] = []
        seen_nodes = {root.id}
        seen_edges: set[str] = set()
        truncated = False
        queue: deque[tuple[str, int]] = deque([(root.id, 0)])

        while queue:
            node_id, depth = queue.popleft()
            if depth >= hops:
                continue
            for edge in self.adjacent(node_id, direction):
                if edge.key in seen_edges:
                    continue
                if len(edges) >= max_edges:
                    truncated = True
                    break
                seen_edges.add(edge.key)
                edges.append((edge, depth + 1))
                neighbour_id = edge.target_id if edge.source_id == node_id else edge.source_id
                if neighbour_id in seen_nodes:
                    continue
                neighbour = self.node(neighbour_id)
                if neighbour is None:
                    continue
                if len(steps) >= max_nodes:
                    truncated = True
                    continue
                seen_nodes.add(neighbour_id)
                steps.append(
                    TraversalStep(
                        node=neighbour,
                        hop=depth + 1,
                        direction=(
                            TopologyDirection.DOWNSTREAM
                            if edge.source_id == node_id
                            else TopologyDirection.UPSTREAM
                        ),
                    )
                )
                queue.append((neighbour_id, depth + 1))
        return steps, edges, truncated


def build_graph(landscape: ProjectLandscape) -> TopologyGraph:
    """Index the graph the canvas is currently rendering."""
    business = active_business_topology(landscape)
    if business is not None:
        return _business_graph(business, landscape.topology_evidence)
    return _scanner_graph(landscape)


def active_business_topology(landscape: ProjectLandscape) -> BusinessTopology | None:
    """The overlay when the canvas would render it, otherwise None.

    Same condition as `landscapeToFlow`: published, validated, and matching the
    current scan. A stale or unvalidated overlay never replaces scanner facts.
    """
    state = landscape.topology_state
    if landscape.business_topology is None or state is None:
        return None
    if not state.validated or state.stale:
        return None
    return landscape.business_topology


def _business_graph(business: BusinessTopology, evidence: TopologyEvidence | None) -> TopologyGraph:
    systems: list[BusinessSystem] = business.systems
    streams: list[BusinessDataStream] = business.data_streams
    solutions = {item.id: item for item in business.integration_solutions}
    objects = {item.id: item for item in business.business_objects}
    # A stream cites the scanner artifacts that implement it by opaque id. The
    # evidence catalogue is the artifact's own bridge back to job names, which is
    # what makes the claim checkable in the answer.
    artifact_names = (
        {item.id: item.name or item.job_id or item.id for item in evidence.artifacts}
        if evidence is not None
        else {}
    )

    nodes = [
        TopologyNode(
            id=system.id,
            name=system.name or system.id,
            kind=system.kind,
            entity=NodeEntity.SYSTEM,
            description=system.description or system.purpose,
            role="",
            aliases=tuple(system.aliases),
            source_roots=(),
            artifact_count=None,
            provenance=NodeProvenance(
                naming=Naming.BUSINESS_OVERLAY,
                claim_status=system.status,
                confidence=system.confidence,
                evidence_ids=tuple(system.evidence_ids),
                scanner_signals=(),
            ),
        )
        for system in systems
    ]
    edges = [
        TopologyEdgeFact(
            key=business_stream_edge_key(stream.id),
            source_id=stream.source_system_id,
            target_id=stream.destination_system_id,
            name=stream.name or stream.id,
            dependency_type="business_data_stream",
            mechanism=", ".join(stream.protocols) or "unknown",
            reliability=stream.requiredness,
            confidence=stream.status,
            evidence_count=len(stream.evidence_ids),
            naming=Naming.BUSINESS_OVERLAY,
            claim_status=stream.status,
            business_objects=tuple(
                objects[object_id].name
                for object_id in stream.business_object_ids
                if object_id in objects
            ),
            protocols=tuple(stream.protocols),
            integrations=tuple(
                solutions[solution_id].name
                for solution_id in stream.integration_solution_ids
                if solution_id in solutions
            ),
            scanner_provenance=tuple(
                artifact_names.get(artifact_id, artifact_id) for artifact_id in stream.artifact_ids
            ),
        )
        for stream in streams
    ]
    return TopologyGraph(view=TopologyView.BUSINESS, nodes=nodes, edges=edges)


def _scanner_graph(landscape: ProjectLandscape) -> TopologyGraph:
    nodes = [_scanner_application_node(item) for item in landscape.applications]
    nodes += [_scanner_system_node(item) for item in landscape.systems]
    edges = [_scanner_edge_fact(edge) for edge in landscape.edges]
    return TopologyGraph(view=TopologyView.SCANNER, nodes=nodes, edges=edges)


def _scanner_application_node(application: LandscapeApplication) -> TopologyNode:
    return TopologyNode(
        id=application.id,
        name=application.label or application.source_root or application.id,
        kind="integration_application",
        entity=NodeEntity.APPLICATION,
        description=application.description,
        role=application.role,
        aliases=(),
        source_roots=tuple(
            application.source_roots
            or ([application.source_root] if application.source_root else [])
        ),
        artifact_count=application.artifact_count,
        provenance=NodeProvenance(
            naming=Naming.SCANNER,
            claim_status="observed",
            confidence=None,
            evidence_ids=(),
            scanner_signals=tuple(
                f"{kind}={count}" for kind, count in sorted(application.kinds_breakdown.items())
            ),
        ),
    )


def _scanner_system_node(system: LandscapeSystem) -> TopologyNode:
    # The signal labels are the practical discriminator between same-named
    # systems (e.g. "LogConnection" vs "LogDBConnection"), so they are part of
    # identity here rather than an optional detail.
    signals = tuple(
        dict.fromkeys(f"{item.signal_type}:{item.label}" for item in system.evidence if item.label)
    )
    return TopologyNode(
        id=system.id,
        name=system.name or system.id,
        kind=system.kind,
        entity=NodeEntity.SYSTEM,
        description="",
        role="",
        aliases=(),
        source_roots=tuple(system.source_roots),
        artifact_count=None,
        provenance=NodeProvenance(
            naming=Naming.SCANNER,
            claim_status="observed",
            confidence=None,
            evidence_ids=(),
            scanner_signals=signals,
        ),
    )


def _scanner_edge_fact(edge: LandscapeEdge) -> TopologyEdgeFact:
    return TopologyEdgeFact(
        key=scanner_edge_key(edge),
        source_id=edge.source,
        target_id=edge.target,
        name=f"{edge.dependency_type} via {edge.mechanism}",
        dependency_type=edge.dependency_type,
        mechanism=edge.mechanism,
        reliability=edge.reliability,
        confidence=edge.confidence,
        evidence_count=edge.evidence_count,
        naming=Naming.SCANNER,
        claim_status="observed",
        business_objects=(),
        protocols=(),
        integrations=(),
        scanner_provenance=tuple(
            dict.fromkeys(
                f"{item.artifact_id}:{item.component_name} ({item.fact_type})"
                for item in edge.provenance
            )
        ),
    )


def scanner_edge_key(edge: LandscapeEdge) -> str:
    """Mirror of `landscapeEdgeKey` for a scanner edge."""
    return EDGE_KEY_SEPARATOR.join(
        (
            edge.source_kind,
            edge.source,
            edge.target_kind,
            edge.target,
            edge.dependency_type,
            edge.mechanism,
            edge.direction,
        )
    )


def business_stream_edge_key(stream_id: str) -> str:
    """Mirror of `landscapeEdgeKey` for an overlay data stream."""
    return f"business-stream{EDGE_KEY_SEPARATOR}{stream_id}"


def _match_tier(node: TopologyNode, needle: str) -> MatchTier | None:
    if not needle:
        return None
    if node.id.casefold() == needle:
        return MatchTier.ID
    name = node.name.casefold()
    if name == needle:
        return MatchTier.EXACT_NAME
    if any(alias.casefold() == needle for alias in node.aliases):
        return MatchTier.ALIAS
    if name.startswith(needle):
        return MatchTier.PREFIX
    if needle in name or needle in node.id.casefold():
        return MatchTier.SUBSTRING
    return None


def name_match_tier(name: str, needle: str) -> MatchTier | None:
    """Tier for an entity identified only by its name.

    Jobs, groups and components have no separate id in the manifest: the name IS
    the identity the canvas navigates by, so `ID` and `EXACT_NAME` collapse into
    one tier here. Kept as the same ladder as `_match_tier` so a single ranked
    candidate list can mix all four entity kinds.
    """
    if not needle:
        return None
    folded = name.casefold()
    if folded == needle:
        return MatchTier.EXACT_NAME
    if folded.startswith(needle):
        return MatchTier.PREFIX
    if needle in folded:
        return MatchTier.SUBSTRING
    # A question written the other way round ("Quintiq to Maximo" for
    # "Quintiq2Maximo_WorkOrderPickedUpXML") shares no substring with the name,
    # so fall back to requiring every significant word of the query to appear in
    # it. Words shorter than three characters are dropped: "to" and "in" occur
    # inside real scanner paths ("__intodoc_zip__") and would match everything.
    # This is the weakest tier on purpose — it finds candidates to disambiguate,
    # it does not resolve one.
    words = [word for word in re.split(r"[^0-9a-z]+", needle) if len(word) >= 3]
    if len(words) > 1 and all(word in folded for word in words):
        return MatchTier.SUBSTRING
    return None


# --- Design index: groups, jobs and components (L3/L4) -----------------------
#
# None of these exist in the landscape artifact. It is a whole-project rollup
# whose only trace of a job is an evidence pointer inside an edge's provenance,
# which is why a job name could not be resolved at all before this index.


class DesignEntity(str, Enum):
    GROUP = "group"
    JOB = "job"
    COMPONENT = "component"


@dataclass(frozen=True)
class DesignGroup:
    """One functional group on the canvas, keyed exactly as the canvas keys it."""

    name: str
    source_roots: tuple[str, ...]
    job_count: int


@dataclass(frozen=True)
class DesignJob:
    name: str
    kind: str
    role: str
    group: str
    source_root: str
    summary: str
    direction: str
    component_count: int
    component_types: tuple[str, ...]
    external_systems: tuple[str, ...]
    calls: tuple[str, ...]
    called_by: tuple[str, ...]


@dataclass(frozen=True)
class DesignComponent:
    name: str
    job: str
    source_root: str
    component_type: str
    role: str
    #: None when the index could not know: the project manifest names a component
    #: but says nothing about whether it has a deeper mapping graph. Reporting
    #: False there would be a claim the artifact does not make.
    has_subgraph: bool | None
    fact_types: tuple[str, ...]


DesignItem = DesignGroup | DesignJob | DesignComponent


@dataclass(frozen=True)
class DesignMatch:
    entity: DesignEntity
    item: DesignItem
    tier: MatchTier

    @property
    def name(self) -> str:
        return self.item.name

    @property
    def match(self) -> str:
        return _MATCH_LABELS[self.tier]

    @property
    def source_root(self) -> str:
        return "" if isinstance(self.item, DesignGroup) else self.item.source_root


class ComponentScope(str, Enum):
    """How completely components were indexed, so an answer can say so.

    `MANIFEST_FACTS` covers only components that touch a channel, endpoint or
    resource — the ones the project manifest names. Full per-job coverage needs
    that job's own graph, which is a deep read and therefore opt-in.
    """

    NONE = "none"
    MANIFEST_FACTS = "project_manifest_facts"
    JOB_GRAPH = "job_graph"


@dataclass
class DesignIndex:
    """Name-resolution index over the project design manifest."""

    groups: list[DesignGroup]
    jobs: list[DesignJob]
    components: list[DesignComponent]
    component_scope: ComponentScope
    component_job: str = ""
    _jobs_by_name: dict[str, DesignJob] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._jobs_by_name = {job.name.casefold(): job for job in self.jobs}

    def job(self, name: str) -> DesignJob | None:
        return self._jobs_by_name.get(name.strip().casefold())

    def search(self, query: str, *, limit: int) -> tuple[list[DesignMatch], bool]:
        """Ranked matches over groups, jobs and indexed components."""
        needle = query.strip().casefold()
        matches: list[DesignMatch] = []
        for entity, items in (
            (DesignEntity.JOB, self.jobs),
            (DesignEntity.GROUP, self.groups),
            (DesignEntity.COMPONENT, self.components),
        ):
            for item in items:
                tier = name_match_tier(item.name, needle)
                if tier is not None:
                    matches.append(DesignMatch(entity=entity, item=item, tier=tier))
        matches.sort(key=lambda item: (-item.tier, item.name.casefold(), item.entity.value))
        return matches[:limit], len(matches) > limit

    def resolve_job(self, name_or_query: str, *, source_root: str | None = None) -> list[DesignJob]:
        """Best-tier job matches only, optionally pinned to one source root.

        Returns every match in the winning tier rather than picking one: a
        composite upload can carry the same design in more than one partition,
        and silently choosing a partition would attribute one copy's wiring to
        another.
        """
        needle = name_or_query.strip().casefold()
        tiered: dict[MatchTier, list[DesignJob]] = {}
        for job in self.jobs:
            if source_root is not None and job.source_root != source_root:
                continue
            tier = name_match_tier(job.name, needle)
            if tier is not None:
                tiered.setdefault(tier, []).append(job)
        for tier in sorted(tiered, reverse=True):
            return sorted(tiered[tier], key=lambda job: (job.name.casefold(), job.source_root))
        return []


def build_design_index(
    graph: ProjectGraph,
    *,
    job_graph: JobGraph | None = None,
) -> DesignIndex:
    """Index the design manifest, optionally with one job's full component list.

    Group naming mirrors `topologyGroupForJob`: the manifest's group path when it
    has one, else the source root, else "Ungrouped". A group target built with
    any other name resolves to nothing on the canvas.
    """
    manifest = graph.manifest
    job_names = _manifest_job_names(manifest)

    groups: dict[str, set[str]] = {}
    group_counts: dict[str, int] = {}
    jobs: list[DesignJob] = []
    for name in sorted(job_names):
        source_root = manifest.job_source_roots.get(name, "")
        group = manifest.job_groups.get(name) or source_root or "Ungrouped"
        group_counts[group] = group_counts.get(group, 0) + 1
        roots = groups.setdefault(group, set())
        if source_root:
            roots.add(source_root)
        metadata = graph.job_metadata.get(name)
        jobs.append(
            DesignJob(
                name=name,
                kind=manifest.artifact_kinds.get(name, "job"),
                role=metadata.artifact_role if metadata is not None else "",
                group=group,
                source_root=source_root,
                summary=(metadata.summary or metadata.purpose) if metadata is not None else "",
                direction=metadata.direction if metadata is not None else "",
                component_count=metadata.component_count if metadata is not None else 0,
                component_types=tuple(metadata.component_types) if metadata is not None else (),
                external_systems=tuple(manifest.job_external_systems.get(name, ())),
                calls=tuple(manifest.job_calls.get(name, ())),
                called_by=tuple(manifest.job_called_by.get(name, ())),
            )
        )

    if job_graph is not None:
        components = _components_from_job_graph(job_graph, manifest)
        scope = ComponentScope.JOB_GRAPH
        scoped_job = job_graph.job_name
    else:
        components = _components_from_manifest_facts(manifest)
        scope = ComponentScope.MANIFEST_FACTS if components else ComponentScope.NONE
        scoped_job = ""

    return DesignIndex(
        groups=[
            DesignGroup(
                name=name,
                source_roots=tuple(sorted(roots)),
                job_count=group_counts.get(name, 0),
            )
            for name, roots in sorted(groups.items())
        ],
        jobs=jobs,
        components=components,
        component_scope=scope,
        component_job=scoped_job,
    )


def _manifest_job_names(manifest: GraphManifest) -> set[str]:
    """Every design the manifest names anywhere.

    Same union the canvas uses: no single map is complete, because an isolated
    job has no calls and a flat (non-composite) scan has no source roots.
    """
    return {
        *manifest.job_groups,
        *manifest.job_source_roots,
        *manifest.job_calls,
        *manifest.job_called_by,
        *manifest.artifact_kinds,
    }


def _components_from_manifest_facts(manifest: GraphManifest) -> list[DesignComponent]:
    facts: dict[tuple[str, str], list[GraphComponentFact]] = {}
    for group in (
        manifest.artifact_channels,
        manifest.artifact_endpoints,
        manifest.artifact_resources,
    ):
        for fact in group:
            if not fact.component_name or not fact.artifact:
                continue
            facts.setdefault((fact.artifact, fact.component_name), []).append(fact)
    return [
        DesignComponent(
            name=component_name,
            job=job_name,
            source_root=manifest.job_source_roots.get(job_name, ""),
            # The manifest names the fact, not the component's Talend type; the
            # fact provenance ("cREST", "tMSSqlInput") is the closest honest
            # stand-in and is labelled as provenance, never as a declared type.
            component_type=next((fact.provenance for fact in items if fact.provenance), ""),
            role="",
            has_subgraph=None,
            fact_types=tuple(dict.fromkeys(fact.fact_type for fact in items)),
        )
        for (job_name, component_name), items in sorted(facts.items())
    ]


def _components_from_job_graph(
    job_graph: JobGraph, manifest: GraphManifest
) -> list[DesignComponent]:
    source_root = manifest.job_source_roots.get(job_graph.job_name, "")
    return [
        DesignComponent(
            name=node.unique_name,
            job=job_graph.job_name,
            source_root=source_root,
            component_type=node.component_type,
            role=node.role,
            has_subgraph=node.has_subgraph,
            fact_types=(),
        )
        for node in job_graph.nodes
    ]


# --- Navigation targets ------------------------------------------------------
#
# `level` must match the frontend `TopologyLevel` union exactly, including the
# id-vs-name split: `integration` selections carry ids, while `group` / `job` /
# `component` carry names plus an optional `sourceRoot`. Built as literal dicts
# so the camelCase key names are visible at the point they are produced.
#
# `sourceRoot` is OMITTED when unknown, never serialised as null: the frontend
# contract declares it optional, and a declared root the manifest cannot confirm
# filters the canvas to nothing.
#
# Every target carries the call's `intent`. `reference` means "this is the entity
# I am talking about"; `navigate` means the user asked to be taken there. The
# default is `reference` all the way down, because a wrongly-navigating chip
# moves the user's canvas out from under them.

#: A relationship target aggregates a system pair's streams. Bounded well below
#: the frontend's 200 because the harness envelope caps every list at 20 items.
MAX_STREAM_KEYS = 20


def node_target(
    node: TopologyNode,
    *,
    intent: NavigationIntent,
    visual_role: VisualRole = VisualRole.SUPPORTING,
) -> dict[str, Any]:
    selection: dict[str, str] = (
        {"kind": "application", "applicationId": node.id}
        if node.entity is NodeEntity.APPLICATION
        else {"kind": "system", "systemId": node.id}
    )
    return _target(
        node.name,
        {"kind": "integration", "selection": selection},
        intent,
        visual_role=visual_role,
    )


def edge_target(
    edge: TopologyEdgeFact,
    label: str,
    *,
    intent: NavigationIntent,
    visual_role: VisualRole = VisualRole.SUPPORTING,
) -> dict[str, Any]:
    return _target(
        label,
        {"kind": "integration", "selection": {"kind": "edge", "edgeKey": edge.key}},
        intent,
        visual_role=visual_role,
    )


def relationship_target(
    *,
    source_id: str,
    target_id: str,
    stream_keys: Iterable[str],
    label: str,
    intent: NavigationIntent,
    visual_role: VisualRole = VisualRole.SUPPORTING,
) -> dict[str, Any]:
    """The aggregated view between two systems — "the view between X and Y".

    Only meaningful in the business view: the canvas aggregates one edge per
    directed system pair in `businessLandscapeToFlow` and nowhere else, so the
    scanner view has no pair node to navigate to. Callers gate on the view for
    the same reason every other target factory respects the active id space.
    """
    return _target(
        label,
        {
            "kind": "integration",
            "selection": {
                "kind": "relationship",
                "source": source_id,
                "target": target_id,
                "streamKeys": list(stream_keys)[:MAX_STREAM_KEYS],
            },
        },
        intent,
        visual_role=visual_role,
    )


def group_target(
    group: DesignGroup,
    *,
    intent: NavigationIntent,
    visual_role: VisualRole = VisualRole.SUPPORTING,
) -> dict[str, Any]:
    level: dict[str, Any] = {"kind": "group", "groupName": group.name}
    # A group spans one source root or several. Pinning it to one of several
    # would filter the canvas to a subset the user did not ask for, so the root
    # is declared only when it is unambiguous.
    if len(group.source_roots) == 1:
        level["sourceRoot"] = group.source_roots[0]
    return _target(group.name, level, intent, visual_role=visual_role)


def job_target(
    job_name: str,
    *,
    source_root: str = "",
    intent: NavigationIntent,
    label: str = "",
    visual_role: VisualRole = VisualRole.SUPPORTING,
) -> dict[str, Any]:
    level: dict[str, Any] = {"kind": "job", "jobName": job_name}
    if source_root:
        level["sourceRoot"] = source_root
    return _target(label or job_name, level, intent, visual_role=visual_role)


def component_target(
    *,
    job_name: str,
    component_name: str,
    source_root: str = "",
    intent: NavigationIntent,
    label: str = "",
    visual_role: VisualRole = VisualRole.SUPPORTING,
    visual_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    level: dict[str, Any] = {
        "kind": "component",
        "jobName": job_name,
        "componentName": component_name,
    }
    if source_root:
        level["sourceRoot"] = source_root
    return _target(
        label or f"{job_name} / {component_name}",
        level,
        intent,
        visual_role=visual_role,
        visual_detail=visual_detail,
    )


def design_target(
    match: DesignMatch,
    *,
    intent: NavigationIntent,
    visual_role: VisualRole = VisualRole.SUPPORTING,
) -> dict[str, Any]:
    """The right target variant for one design match, by its entity kind."""
    item = match.item
    if isinstance(item, DesignGroup):
        return group_target(item, intent=intent, visual_role=visual_role)
    if isinstance(item, DesignJob):
        return job_target(
            item.name,
            source_root=item.source_root,
            intent=intent,
            visual_role=visual_role,
        )
    return component_target(
        job_name=item.job,
        component_name=item.name,
        source_root=item.source_root,
        intent=intent,
        visual_role=visual_role,
    )


def _target(
    label: str,
    level: dict[str, Any],
    intent: NavigationIntent,
    *,
    visual_role: VisualRole = VisualRole.SUPPORTING,
    visual_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target: dict[str, Any] = {
        "label": label,
        "level": level,
        "intent": intent.value,
        "visual_role": visual_role.value,
    }
    if visual_detail is not None:
        target["visual_detail"] = visual_detail
    return target
