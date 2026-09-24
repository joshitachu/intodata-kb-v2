"""The seven read-only topology tools.

Every handler returns the pinned payload shape: a bounded `content` projection
plus `targets` the canvas can navigate to. The landscape artifact is a whole
project document (a real composite upload carries hundreds of channels and
dozens of systems), a real job carries up to ~250 components, and the field
lineage carries 2 222 mappings, so nothing here is unbounded — the caps below
are part of the contract, declared once and enforced at every projection.

Three depth levels, three artifact families. `get_topology_overview`,
`find_topology_node`, `get_topology_node` and `list_topology_dependencies`
answer from the landscape rollup and the project design manifest. `get_job_detail`
and `get_component_detail` open a per-job or per-component document.
`get_field_lineage` opens the field-lineage artifact, which is the only place
column-to-column movement and its transformation expression exist. The split is
the reason the landscape-level handlers must not reach deeper: a structural
question that the landscape cannot answer has to come back as "the topology
artifact does not describe that at this level", not as an answer silently
sourced from somewhere else.

Where a projection cannot show everything it found, it says how much it found
and how far into it this page reached. That is not decoration: `get_job_detail`
clips 251 components to 12 and `get_topology_overview` clips its diagnostics on
every real project, and a listing that reports neither the total nor a way past
the first page reads as a complete answer when it is a twentieth of one.

Every handler accepts a NAME. The agent loop has four iterations for the whole
turn, shared with the runtime tools, so forcing a find -> get round trip would
cost a quarter of the budget to learn something this module already knows.

`content` nesting is kept deliberately shallow. The harness bounds the envelope
at six container levels counted from `data`, so a leaf list nested three deep
under `content` is replaced by a depth marker. Leaf collections that would land
that deep are joined into one bounded string instead.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from src.config import settings
from src.topology.graph import (
    ComponentScope,
    DesignComponent,
    DesignEntity,
    DesignGroup,
    DesignIndex,
    DesignJob,
    DesignMatch,
    EdgeMatch,
    EntityResolution,
    MatchTier,
    Naming,
    NodeEntity,
    NodeMatch,
    TopologyEdgeFact,
    TopologyGraph,
    TopologyNode,
    TopologyView,
    active_business_topology,
    build_design_index,
    business_stream_edge_key,
    build_graph,
    component_target,
    design_target,
    edge_target,
    group_target,
    job_target,
    name_match_tier,
    node_target,
    relationship_target,
)
from src.topology.models import (
    BusinessTopology,
    ComponentGraph,
    ComponentGraphTable,
    FieldLineageEdge,
    FindTopologyNodeInput,
    GetComponentDetailInput,
    GetFieldLineageInput,
    GetJobDetailInput,
    GetTopologyNodeInput,
    GetTopologyOverviewInput,
    JobFieldLineage,
    JobGraph,
    JobGraphComponent,
    JobGraphConnection,
    JobGraphParamTable,
    LandscapeDiagnostic,
    ListTopologyDependenciesInput,
    NavigationIntent,
    ProjectLandscape,
    TopologyDirection,
    VisualRole,
)
from src.topology.provider import (
    LandscapeArtifact,
    TopologyArtifactUnavailableError,
    TopologyDesignUnavailableError,
    TopologyMappingUnavailableError,
    TopologyPayloadError,
    TopologyProvider,
)

#: Result caps. Enforced, not advisory: each projection reports whether it hit
#: one so the model can say "showing N of M" instead of implying completeness.
MAX_CANDIDATES = 10
# Eight, not ten: the aggregated per-counterpart pair views added below cover a
# hub's whole relationship set in three entries, which is more informative for a
# hub than two more individual streams — and the real total is always reported.
MAX_NODE_EDGES = 8
MAX_NODE_EVIDENCE = 6
MAX_NAME_MATCHED_SCANNER_ENTITIES = 3
MAX_ADJACENT_LABELS = 3
MAX_DEPENDENCY_NODES = 15
# A hub system in a real composite estate has ~30 relationships. A dependency
# listing therefore uses the compact edge projection and a tighter edge cap:
# with the full projection a single one-hop answer on a hub already exceeded
# `runtime_tool_result_max_chars`, and the size backstop would have thrown away
# the dependency list the caller asked for.
MAX_DEPENDENCY_EDGES = 15
MAX_TARGETS = 10
MAX_BREAKDOWN_ENTRIES = 12
MAX_OVERVIEW_ITEMS = 8
MAX_DIAGNOSTICS = 8
MAX_TEXT_CHARS = 300

# Depth caps, tuned against real data. The richest real job in the werkorder
# upload has 251 components and 201 connections and serializes to 129 KB raw;
# the richest real mapping component serializes to 27 KB. Both have to fit,
# together with their targets, inside the 12 000-char tool-result ceiling, so
# these are listing caps plus counted breakdowns rather than generous limits.
MAX_JOB_COMPONENTS = 12
MAX_JOB_CONNECTIONS = 12
MAX_RELATED_JOBS = 8
MAX_COMPONENT_TARGETS = 6
MAX_COMPONENT_WIRING = 8
MAX_MAPPING_TABLES = 8
MAX_MAPPING_EXPRESSIONS = 10
MAX_MAPPING_REFS = 4
#: Mapping expressions are code, and a single real one runs to several hundred
#: characters. Clipped harder than prose: the model needs to see the shape of the
#: transformation, and ten whole expressions would consume the entire budget.
MAX_EXPRESSION_CHARS = 180
MAX_PARAM_ENTRIES = 6
#: Row-structured parameters (`param_tables`). A component's decision logic
#: lives here rather than in the scalar params -- the routing key a Camel route
#: later branches on is a row of a cSetHeader VALUES table, not a scalar -- so
#: these are budgeted deliberately instead of sharing the scalar cap, which a
#: component with six scalar params would have exhausted before reaching them.
#: Measured over the pluripharm scan, p90 is one table of seven rows per
#: component and the worst is 74 rows, so the caps below cover the p90 whole and
#: state the count they elided on the tail.
MAX_PARAM_TABLES = 3
MAX_PARAM_TABLE_ROWS = 6
#: One rendered row (`NAME=... | LANGUAGE=... | EXPRESSION=...`). Kept well under
#: the envelope's 500-char per-string ceiling so the harness never clips a row
#: mid-expression -- a half-XPath reads as a whole one.
MAX_PARAM_ROW_CHARS = 240
#: Whole-page budget for the parameter blocks of a batched `get_job_detail`, and
#: the ceiling any single component may take out of it. Twelve components at the
#: per-component ceiling would exceed the 12 000-char result ceiling, and
#: overrunning it is worse than paging: the size backstop in `client.py` drops
#: EVERY collection in the payload, including the component list that was asked
#: for. Real data is far below both (the whole 41-component collector design
#: renders to ~2.3 KB of settings), so the budget only bites on the outliers.
MAX_JOB_PARAM_CHARS = 4000
MAX_COMPONENT_PARAM_CHARS = 900
#: A single-component read can afford more than a component's share of a page:
#: the richest real component detail (the 27 KB mapping artifact) serializes to
#: 5 959 chars of the 12 000 ceiling, so this is the one call where a long
#: routing or mapping table can be shown nearly whole.
MAX_COMPONENT_DETAIL_PARAM_CHARS = 2400
#: Held back from the result ceiling when sizing a batched settings read. The
#: real page without settings already reaches 10 KB of the 12 KB ceiling on the
#: richest designs, so the batch is sized against what is actually left rather
#: than against the fixed budget above; this reserve covers the targets list
#: (not yet built at that point) and the envelope's own keys.
JOB_PARAM_BUDGET_RESERVE = 2000
#: Aggregated system-pair views to offer from one system. Three covers a hub's
#: main counterparts and leaves room for the per-stream edge targets.
MAX_PAIR_TARGETS = 3
#: Field-lineage caps. `scan/lineage.json` carries 2 222 mappings for the real
#: werkorder project and a single integration spans up to 13 designs, so the
#: rollup IS the answer here and the mapping list only illustrates it.
#: Twelve concrete mappings, the same page size the job-detail listings use, so
#: one offset behaves the same way across every paged tool.
MAX_LINEAGE_EDGES = 12
#: Target tables named individually. The real RHM2SF design writes to 7 distinct
#: tables (`out1`, `PlanningRealization`, `Main1`, `FileLog`, ...), so ten shows
#: a whole design's write surface; the real total is always reported beside it.
MAX_LINEAGE_TARGET_TABLES = 10
#: Designs named individually when a group covers several. Thirteen is the
#: largest real single-integration design family, so eight shows most of one and
#: the rest are counted.
MAX_LINEAGE_JOBS_LISTED = 8
#: Runtime refs per mapping (`TalendDate.getDate`,
#: `tXMLMap:output_field_without_expression`). Four is enough to show WHY a
#: mapping is runtime-determined; they are joined into one string rather than
#: nested as a list, as everywhere else at this depth.
MAX_LINEAGE_DYNAMIC_REFS = 4
#: Provenance entries listed per relationship, alongside the real total. Slightly
#: tighter than the general evidence cap to make room for the pair targets. In the
#: scanner view these are `design:component (fact)` strings whose design name
#: `get_job_detail` accepts; in the overlay view they can be opaque artifact ids,
#: which is why the count matters as much as the entries.
MAX_EDGE_PROVENANCE = 4


@dataclass(frozen=True)
class TopologyToolPayload:
    """The `data` value of a successful topology tool result."""

    content: dict[str, Any]
    targets: list[dict[str, Any]]
    truncated: bool = False

    def as_data(self) -> dict[str, Any]:
        return {"content": self.content, "targets": self.targets[:MAX_TARGETS]}

    @property
    def hit_cap(self) -> bool:
        """Whether any cap elided content, including the target cap."""
        return self.truncated or len(self.targets) > MAX_TARGETS


#: `_Page` is generic so a paged listing keeps its element type: the caller then
#: projects the items with the right per-item function and a mismatch is a type
#: error rather than a runtime surprise.
PagedItem = TypeVar("PagedItem")


@dataclass(frozen=True)
class _Page(Generic[PagedItem]):
    """One window into a listing, with the arithmetic needed to say so honestly.

    Every listing in this module is clipped, and a clipped listing that reports
    only its items reads as a complete answer. This carries the four facts a
    caller needs to know it is not: how many exist, how many are here, whether
    more follow, and which offset reaches them. `next_offset` is None rather
    than a number when nothing follows, so a model cannot page into emptiness.

    The ordering is the caller's: paging is only stable if the sequence handed
    in is deterministically ordered, which is why `get_job_detail` keeps its
    `_component_order` ranking as the paging order rather than re-sorting per
    page.
    """

    items: list[PagedItem]
    offset: int
    total: int

    @classmethod
    def of(cls, items: Sequence[PagedItem], *, offset: int, limit: int) -> _Page[PagedItem]:
        start = max(offset, 0)
        return cls(items=list(items[start : start + limit]), offset=start, total=len(items))

    @property
    def shown(self) -> int:
        return len(self.items)

    @property
    def has_more(self) -> bool:
        return self.offset + self.shown < self.total

    @property
    def next_offset(self) -> int | None:
        return self.offset + self.shown if self.has_more else None

    @property
    def past_end(self) -> bool:
        """An offset beyond the whole listing, which returns nothing silently."""
        return self.total > 0 and self.offset >= self.total


async def get_topology_overview(
    params: GetTopologyOverviewInput,
    *,
    provider: TopologyProvider,
    access_token: str | None,
) -> TopologyToolPayload:
    intent = params.navigation_intent
    artifact = await provider.get_landscape(project_id=params.project_id, access_token=access_token)
    landscape = artifact.landscape
    graph = build_graph(landscape)
    business = landscape.business_topology

    # Both listings are paged by one offset. Before this they were clipped on
    # every real project with no way to read past the clip: the werkorder upload
    # has 38 diagnostics against a cap of 8, so 30 of them were unreachable.
    applications = _Page.of(landscape.applications, offset=params.offset, limit=MAX_OVERVIEW_ITEMS)
    diagnostics = _Page.of(_by_severity(landscape), offset=params.offset, limit=MAX_DIAGNOSTICS)

    diagnostics_by_severity = _counted(item.severity or "info" for item in landscape.diagnostics)
    content: dict[str, Any] = {
        "project_id": artifact.project_id,
        "scan_fingerprint": artifact.fingerprint.scan,
        # Which of the two graphs the canvas renders, and therefore which id
        # space the targets in every topology answer belong to.
        "active_view": graph.view.value,
        "topology_state": _topology_state_content(landscape),
        "scanner_facts": {
            "systems": len(landscape.systems),
            "applications": len(landscape.applications),
            "edges": len(landscape.edges),
            "channels": len(landscape.channels),
            "interfaces": len(landscape.interfaces),
            "diagnostics": len(landscape.diagnostics),
            "unmapped_facts": len(landscape.unmapped),
            "systems_by_kind": _counted(item.kind or "unknown" for item in landscape.systems),
            "application_roles": _counted(
                item.role or "unknown" for item in landscape.applications
            ),
            "edges_by_dependency_type": _counted(
                item.dependency_type or "unknown" for item in landscape.edges
            ),
            "edges_by_mechanism": _counted(item.mechanism or "unknown" for item in landscape.edges),
            "channels_by_type": _counted(
                item.channel_type or "unknown" for item in landscape.channels
            ),
            "interfaces_by_type": _counted(
                item.endpoint_type or "unknown" for item in landscape.interfaces
            ),
            "top_applications": [
                {
                    "id": item.id,
                    "label": item.label or item.source_root or item.id,
                    "role": item.role,
                    "artifact_count": item.artifact_count,
                    "source_root": item.source_root,
                }
                for item in applications.items
            ],
            # `applications` above is the real total; these say what this page of
            # `top_applications` actually reached.
            "applications_shown": applications.shown,
            "applications_offset": applications.offset,
            "applications_has_more": applications.has_more,
            "applications_next_offset": applications.next_offset,
        },
        "business_overlay": _business_overlay_content(business),
        "diagnostics": {
            "by_severity": diagnostics_by_severity,
            "top": [
                {
                    "kind": item.kind,
                    "severity": item.severity,
                    "description": _clipped(item.description),
                }
                for item in diagnostics.items
            ],
            "total": diagnostics.total,
            "shown": diagnostics.shown,
            "offset": diagnostics.offset,
            "has_more": diagnostics.has_more,
            "next_offset": diagnostics.next_offset,
        },
        "graph": {
            "nodes": len(graph.nodes),
            "relationships": len(graph.edges),
        },
    }
    busiest = sorted(graph.nodes, key=lambda node: (-graph.degree(node.id), node.name.casefold()))
    content["graph"]["navigable_targets_shown"] = min(len(busiest), MAX_TARGETS)
    if applications.past_end or diagnostics.past_end:
        content["page_note"] = (
            f"offset {params.offset} is past the end of one or both listings; "
            "the totals beside each list are the real counts."
        )
    truncated = (
        applications.has_more
        or diagnostics.has_more
        or len(landscape.applications) > MAX_OVERVIEW_ITEMS
        or len(landscape.diagnostics) > MAX_DIAGNOSTICS
        or len(busiest) > MAX_TARGETS
    )
    return TopologyToolPayload(
        content=content,
        targets=[node_target(node, intent=intent) for node in busiest[:MAX_TARGETS]],
        truncated=truncated,
    )


async def find_topology_node(
    params: FindTopologyNodeInput,
    *,
    provider: TopologyProvider,
    access_token: str | None,
) -> TopologyToolPayload:
    """Search all four entity kinds: systems, applications, groups, jobs, components.

    Before this searched only the landscape rollup, which has no job or component
    nodes at all — so a real job name like `Quintiq2Maximo_WorkOrderPickedUpXML`
    could not be found, and a question about it fell through to whatever other
    source was willing to answer.
    """
    intent = params.navigation_intent
    artifact = await provider.get_landscape(project_id=params.project_id, access_token=access_token)
    graph = build_graph(artifact.landscape)
    landscape_matches, landscape_more = graph.search(params.query, limit=MAX_CANDIDATES)

    design = await _design_lookup(params, provider=provider, access_token=access_token)
    design_matches, design_more = (
        design.index.search(params.query, limit=MAX_CANDIDATES)
        if design.index is not None
        else ([], False)
    )

    buckets: dict[str, list[_Candidate]] = {}
    for match in landscape_matches:
        buckets.setdefault(match.node.entity.value, []).append(
            _Candidate(
                entity=match.node.entity.value,
                tier=int(match.tier),
                content=_landscape_candidate_content(match, graph),
                target=node_target(match.node, intent=intent),
            )
        )
    for match in design_matches:
        buckets.setdefault(match.entity.value, []).append(
            _Candidate(
                entity=match.entity.value,
                tier=int(match.tier),
                content=_design_candidate_content(match),
                target=design_target(match, intent=intent),
            )
        )

    merged, over_cap = _fair_merge(buckets, limit=MAX_CANDIDATES)
    truncated = over_cap or landscape_more or design_more
    exact = [item for item in merged if item.content.get("match") == "exact_name"]
    content = {
        "project_id": artifact.project_id,
        "query": params.query,
        "active_view": graph.view.value,
        "match_count": len(merged),
        # More than one exact name match is a real property of the estate, not a
        # search defect: never collapse it. It now spans four entity kinds, so
        # every candidate names its own kind and source root.
        "ambiguous": len(exact) > 1,
        "matched_kinds": sorted({item.entity for item in merged}),
        "candidates": [item.content for item in merged],
        "more_matches_exist": truncated,
        "design_coverage": design.coverage(),
    }
    targets = [item.target for item in merged]
    # A search result becomes the visual subject only when resolution is exact
    # enough to leave one candidate. Candidate lists are choices, not focus
    # instructions, even when one item happens to sort first.
    if len(targets) == 1:
        targets[0] = {**targets[0], "visual_role": VisualRole.PRIMARY.value}
    return TopologyToolPayload(content=content, targets=targets, truncated=truncated)


async def get_topology_node(
    params: GetTopologyNodeInput,
    *,
    provider: TopologyProvider,
    access_token: str | None,
) -> TopologyToolPayload:
    """One system, integration application, OR relationship, with its evidence.

    Handles a relationship as well as a node because that is the call the client
    already makes: when a user selects a CONNECTION on the canvas, the page
    scope carries `{kind: "edge", entity_id: "<edge key>"}` and the model passes
    that straight into `name_or_id`. Resolution used to look at nodes only, so
    the call came back not_found and the answer that followed was about a
    different integration, with nothing saying one had been substituted.
    Extending this handler rather than adding a seventh operation is deliberate:
    it fixes the existing call site with no change to what steers the model.
    """
    intent = params.navigation_intent
    artifact = await provider.get_landscape(project_id=params.project_id, access_token=access_token)
    graph = build_graph(artifact.landscape)
    resolution = graph.resolve_any(params.name_or_id)

    edge_match = resolution.only_edge
    if edge_match is not None:
        return _relationship_payload(
            artifact=artifact,
            graph=graph,
            match=edge_match,
            query=params.name_or_id,
            intent=intent,
        )

    node_match = resolution.only_node
    if node_match is None:
        return _unresolved_payload(
            artifact=artifact,
            graph=graph,
            query=params.name_or_id,
            resolution=resolution,
            intent=intent,
        )

    node = node_match.node
    edges = graph.adjacent(node.id, TopologyDirection.BOTH)
    pairs = _system_pairs(node, edges, graph)
    content = {
        "project_id": artifact.project_id,
        "active_view": graph.view.value,
        "resolved": True,
        "matched_on": node_match.match,
        # Self-describing: this handler can now return a relationship instead,
        # and every sentence downstream depends on knowing which one it got.
        "matched_entity": node.entity.value,
        "node": _node_content(node, graph),
        "evidence": _node_evidence_content(node, artifact.landscape),
        "relationship_count": len(edges),
        "relationships": [_edge_content(edge, graph) for edge in edges[:MAX_NODE_EDGES]],
        "relationships_truncated": len(edges) > MAX_NODE_EDGES,
        # The aggregated per-counterpart views, named so "the view between X and
        # Y" is answerable rather than only describable.
        "counterpart_views": [
            f"{graph.label(source)} -> {graph.label(target)} ({len(keys)} stream(s))"
            for (source, target), keys in pairs
        ],
    }
    # Pair views come before the per-stream edges: a hub system has more edges
    # than the target cap allows, and "the view between X and Y" is the request
    # that has no other expressible target, so it must not be the one dropped.
    targets = [node_target(node, intent=intent, visual_role=VisualRole.PRIMARY)]
    targets += [
        relationship_target(
            source_id=source,
            target_id=target,
            stream_keys=keys,
            label=f"{graph.label(source)} -> {graph.label(target)}",
            intent=intent,
            visual_role=VisualRole.PATH,
        )
        for (source, target), keys in pairs
    ]
    targets += [
        edge_target(
            edge,
            f"{graph.label(edge.source_id)} -> {graph.label(edge.target_id)}",
            intent=intent,
            visual_role=VisualRole.PATH,
        )
        for edge in edges[:MAX_NODE_EDGES]
    ]
    return TopologyToolPayload(
        content=content, targets=targets, truncated=len(edges) > MAX_NODE_EDGES
    )


async def list_topology_dependencies(
    params: ListTopologyDependenciesInput,
    *,
    provider: TopologyProvider,
    access_token: str | None,
) -> TopologyToolPayload:
    intent = params.navigation_intent
    artifact = await provider.get_landscape(project_id=params.project_id, access_token=access_token)
    graph = build_graph(artifact.landscape)
    resolution = graph.resolve_any(params.name_or_id)

    edge_match = resolution.only_edge
    if edge_match is not None:
        # A relationship has no dependency set of its own; its endpoints do.
        # Naming them beats answering not_found, which is what sent the model
        # looking for another subject to talk about.
        return _relationship_endpoints_payload(
            artifact=artifact,
            graph=graph,
            match=edge_match,
            query=params.name_or_id,
            intent=intent,
        )

    node_match = resolution.only_node
    if node_match is None:
        return _unresolved_payload(
            artifact=artifact,
            graph=graph,
            query=params.name_or_id,
            resolution=resolution,
            intent=intent,
        )

    root = node_match.node
    steps, edges, truncated = graph.traverse(
        root,
        direction=params.direction,
        hops=params.hops,
        max_nodes=MAX_DEPENDENCY_NODES,
        max_edges=MAX_DEPENDENCY_EDGES,
    )
    content = {
        "project_id": artifact.project_id,
        "active_view": graph.view.value,
        "resolved": True,
        "root": _node_content(root, graph),
        "direction": params.direction.value,
        "hops": params.hops,
        "dependency_count": len(steps),
        "dependencies": [
            {
                "id": step.node.id,
                "name": step.node.name,
                "kind": step.node.kind,
                "entity": step.node.entity.value,
                "hop": step.hop,
                "direction": step.direction.value,
                # Claim strength only: a neighbour's own evidence ids belong to
                # get_topology_node, and carrying them here blew the size budget.
                "naming": step.node.provenance.naming.value,
                **_establishment_content(step.node.provenance.claim_status),
                "confidence": step.node.provenance.confidence,
            }
            for step in steps
        ],
        "relationships": [
            {**_compact_edge_content(edge, graph), "hop": hop} for edge, hop in edges
        ],
        "truncated": truncated,
    }
    targets = [node_target(root, intent=intent, visual_role=VisualRole.PRIMARY)] + [
        node_target(step.node, intent=intent, visual_role=VisualRole.PATH) for step in steps
    ]
    return TopologyToolPayload(content=content, targets=targets, truncated=truncated)


# --- Depth tools: jobs and components ----------------------------------------


async def get_job_detail(
    params: GetJobDetailInput,
    *,
    provider: TopologyProvider,
    access_token: str | None,
) -> TopologyToolPayload:
    """One job's components, wiring, orchestration and health-relevant facts.

    Two reads: the project design manifest resolves the name (and supplies the
    group, source root and call graph), then that job's own component graph is
    opened. The second read is the first deep fetch in the whole package, and it
    happens only because the question named a job.

    Paged. The richest real design has 251 components and 201 connections
    against listing caps of 12, so a single fixed page turns the drill-down this
    tool exists for into a dead end. `offset` advances both listings, and every
    listing reports what it showed against what exists.

    `include_parameters` turns the component listing into a batch settings read
    (see `GetJobDetailInput.include_parameters` for why the batch lives here and
    not on `get_component_detail`). The page size does not change, because the
    settings themselves are small on real data; what bounds the batch is a
    character budget across the page, and whatever it could not fit is counted.
    """
    intent = params.navigation_intent
    design = await _design_lookup(params, provider=provider, access_token=access_token)
    if design.index is None:
        return _no_design_manifest_payload(params.project_id, design)

    jobs = design.index.resolve_job(params.name_or_id, source_root=params.source_root)
    if len(jobs) != 1:
        return _ambiguous_design_payload(
            project_id=params.project_id,
            query=params.name_or_id,
            entity=DesignEntity.JOB,
            items=list(jobs),
            intent=intent,
        )

    job = jobs[0]
    job_graph, unavailable = await _job_graph(
        provider, project_id=params.project_id, job_name=job.name, access_token=access_token
    )
    components = list(job_graph.nodes) if job_graph is not None else []
    # `_component_order` is the paging order, not just the display order: it is
    # total and deterministic, so page 2 cannot repeat or skip an entry from
    # page 1, and page 1 still leads with the drillable and external-facing
    # components a follow-up question is actually about.
    ordered = sorted(components, key=_component_order)
    edges = list(job_graph.edges) if job_graph is not None else []
    component_page = _Page.of(ordered, offset=params.offset, limit=MAX_JOB_COMPONENTS)
    connection_page = _Page.of(edges, offset=params.offset, limit=MAX_JOB_CONNECTIONS)
    mapping_components = [item.unique_name for item in components if item.has_subgraph]

    content: dict[str, Any] = {
        "project_id": params.project_id,
        "resolved": True,
        "source": "project_design_manifest",
        "job": _job_content(job),
        "orchestration": {
            "calls": list(job.calls[:MAX_RELATED_JOBS]),
            "calls_total": len(job.calls),
            "called_by": list(job.called_by[:MAX_RELATED_JOBS]),
            "called_by_total": len(job.called_by),
        },
        "component_graph_available": job_graph is not None,
        "component_count": len(components) or job.component_count,
        "components_by_role": _counted(item.role or "unknown" for item in components),
        "components": [_component_summary(item) for item in component_page.items],
        "components_shown": component_page.shown,
        "components_offset": component_page.offset,
        "components_has_more": component_page.has_more,
        "components_next_offset": component_page.next_offset,
        # Retained beside the paging fields: it answers "did the whole list fit
        # in one page", which is what an answer's hedging depends on, while
        # `components_has_more` answers "is there another page after this one".
        "components_truncated": len(components) > MAX_JOB_COMPONENTS,
        "connection_count": len(edges),
        "connections": [_connection_content(edge) for edge in connection_page.items],
        "connections_shown": connection_page.shown,
        "connections_offset": connection_page.offset,
        "connections_has_more": connection_page.has_more,
        "connections_next_offset": connection_page.next_offset,
        "connections_truncated": len(edges) > MAX_JOB_CONNECTIONS,
        "health_relevant": _health_facts(components, edges),
        "mapping_components": mapping_components[:MAX_COMPONENT_TARGETS],
        # Whole-design fact, not a page fact: the mapping components are where
        # get_field_lineage and get_component_detail lead next.
        "mapping_component_count": len(mapping_components),
        "component_settings_included": params.include_parameters,
        "page_note": (
            "components and connections are paged together by one offset; each "
            "list reports its own total, shown count and next_offset."
        ),
    }
    if params.include_parameters:
        # Sized against what the rest of this payload actually left, not against
        # a fixed allowance: the listings above already reach 10 KB of the 12 KB
        # ceiling on the richest real designs, and overrunning it would cost the
        # component list itself (the size backstop drops every collection).
        listed, settings_omitted = _component_listing(
            component_page.items, budget=_batch_param_budget(content)
        )
        content["components"] = listed
        if settings_omitted:
            content["component_settings_omitted"] = settings_omitted
            content["component_settings_omitted_note"] = (
                f"{settings_omitted} of the components shown had their settings "
                "elided to keep this result inside its size budget; read those "
                "with get_component_detail."
            )
    elif any(item.has_settings for item in component_page.items):
        # Steer the caller into the batch instead of into a sweep. The measured
        # failure was 15 single-component calls on one design, so an agent that
        # cannot see this option costs an order of magnitude more iterations
        # than the answer needs.
        content["component_settings_note"] = (
            "Component settings (configured parameters, routing/header/mapping "
            "tables) are not in this listing. Call get_job_detail again with "
            "include_parameters=true to read them for every component on a "
            "page, rather than one get_component_detail call per component."
        )
    if component_page.past_end or connection_page.past_end:
        content["page_note"] = (
            f"offset {params.offset} is past the end of one or both listings for "
            "this design; the counts beside each list are the real totals."
        )
    if unavailable:
        content["component_graph_note"] = unavailable

    targets = [
        job_target(
            job.name,
            source_root=job.source_root,
            intent=intent,
            visual_role=VisualRole.PRIMARY,
        ),
        group_target(
            DesignGroup(
                name=job.group,
                source_roots=(job.source_root,) if job.source_root else (),
                job_count=0,
            ),
            intent=intent,
        ),
    ]
    targets += [
        component_target(
            job_name=job.name,
            component_name=item.unique_name,
            source_root=job.source_root,
            intent=intent,
            label=item.display_label or item.unique_name,
        )
        # Targets follow the page. A chip set frozen on page 1 would let a user
        # read about a component on page 3 and have nothing to click.
        for item in component_page.items[:MAX_COMPONENT_TARGETS]
    ]
    truncated = (
        len(components) > MAX_JOB_COMPONENTS
        or len(edges) > MAX_JOB_CONNECTIONS
        or len(job.calls) > MAX_RELATED_JOBS
        or len(job.called_by) > MAX_RELATED_JOBS
    )
    return TopologyToolPayload(content=content, targets=targets, truncated=truncated)


async def get_component_detail(
    params: GetComponentDetailInput,
    *,
    provider: TopologyProvider,
    access_token: str | None,
) -> TopologyToolPayload:
    """One component's role, wiring and — where it exists — its mapping graph.

    Most components publish no deeper mapping graph, which is a documented
    outcome rather than a failure. The payload says so explicitly instead of
    returning an empty mapping that reads like "no transformations".
    """
    intent = params.navigation_intent
    design = await _design_lookup(params, provider=provider, access_token=access_token)
    if design.index is None:
        return _no_design_manifest_payload(params.project_id, design)

    jobs = design.index.resolve_job(params.job, source_root=params.source_root)
    if len(jobs) != 1:
        return _ambiguous_design_payload(
            project_id=params.project_id,
            query=params.job,
            entity=DesignEntity.JOB,
            items=list(jobs),
            intent=intent,
        )
    job = jobs[0]

    job_graph, unavailable = await _job_graph(
        provider, project_id=params.project_id, job_name=job.name, access_token=access_token
    )
    if job_graph is None:
        return TopologyToolPayload(
            content={
                "project_id": params.project_id,
                "resolved": False,
                "reason": "component_graph_unavailable",
                "job": job.name,
                "query": params.component,
                "note": unavailable,
            },
            targets=[job_target(job.name, source_root=job.source_root, intent=intent)],
        )

    nodes = _resolve_component(job_graph, params.component)
    if len(nodes) != 1:
        return _ambiguous_design_payload(
            project_id=params.project_id,
            query=params.component,
            entity=DesignEntity.COMPONENT,
            items=[
                DesignComponent(
                    name=item.unique_name,
                    job=job.name,
                    source_root=job.source_root,
                    component_type=item.component_type,
                    role=item.role,
                    has_subgraph=item.has_subgraph,
                    fact_types=(),
                )
                for item in nodes
            ],
            intent=intent,
        )

    node = nodes[0]
    inbound = [edge for edge in job_graph.edges if edge.target == node.unique_name]
    outbound = [edge for edge in job_graph.edges if edge.source == node.unique_name]
    mapping, visual_detail = await _mapping_content(
        provider,
        project_id=params.project_id,
        job_name=job.name,
        component=node,
        access_token=access_token,
    )

    content: dict[str, Any] = {
        "project_id": params.project_id,
        "resolved": True,
        "source": "job_component_graph",
        "job": job.name,
        "source_root": job.source_root,
        "component": _component_content(node),
        "inbound_count": len(inbound),
        "inbound": [_connection_content(edge) for edge in inbound[:MAX_COMPONENT_WIRING]],
        "outbound_count": len(outbound),
        "outbound": [_connection_content(edge) for edge in outbound[:MAX_COMPONENT_WIRING]],
    }
    content.update(mapping)

    targets = [
        component_target(
            job_name=job.name,
            component_name=node.unique_name,
            source_root=job.source_root,
            intent=intent,
            label=node.display_label or node.unique_name,
            visual_role=VisualRole.PRIMARY,
            visual_detail=visual_detail,
        ),
        job_target(job.name, source_root=job.source_root, intent=intent),
    ]
    if node.subjob_target:
        # A tRunJob portal: the component IS a hop to another design, and the
        # user's next question is almost always about that design.
        targets.append(
            job_target(
                node.subjob_target,
                intent=intent,
                visual_role=VisualRole.PATH,
            )
        )
    truncated = bool(
        len(inbound) > MAX_COMPONENT_WIRING
        or len(outbound) > MAX_COMPONENT_WIRING
        or content.get("mapping_truncated")
    )
    return TopologyToolPayload(content=content, targets=targets, truncated=truncated)


async def get_field_lineage(
    params: GetFieldLineageInput,
    *,
    provider: TopologyProvider,
    access_token: str | None,
) -> TopologyToolPayload:
    """Field-level movement for one design or one whole functional group.

    The only handler that reaches column-to-column facts: which source column
    lands in which target column, through which expression, in which component.
    `get_component_detail` reaches one mapping component; this reaches a whole
    design or a whole integration.

    Aggregates rather than dumps. One integration spans up to 13 designs and
    hundreds of mappings, and the answer may render in a ~360px panel, so the
    rollup is the answer and the mapping list illustrates it.

    Reports the runtime-determined share as a first-class number. Around 39% of
    the corpus is `dynamic` or otherwise unresolved -- the expression is decided
    at run time and the scan cannot establish the source column -- and an answer
    built from the confident 60% alone understates the integration while sounding
    complete. Those mappings exist; they are counted here, never dropped.

    Deliberately does NOT resolve the job name against the design manifest
    first. Verified on the real composite upload: lineage job names and manifest
    design names diverge (`VolkerVessels_..._VQ_RHM2SF___intod-31f9ad59e878` in
    the lineage against `..._intod-249ee1cc0d2b` in the manifest), so gating on
    manifest resolution would answer "not in the scan" for a design that has
    lineage. The name goes upstream as given and upstream's own 404 is the
    honest negative.
    """
    intent = params.navigation_intent
    artifact = await provider.get_job_lineage(
        project_id=params.project_id,
        jobs=(params.job,) if params.job else (),
        group=params.group,
        offset=params.offset,
        # The page size is this package's cap, not the model's choice: a 200-row
        # page would be discarded by the result-size backstop.
        limit=MAX_LINEAGE_EDGES,
        access_token=access_token,
    )
    lineage = artifact.lineage
    rollup = _lineage_rollup(lineage)
    # Upstream's own `returned` is not trusted over what is actually projected:
    # a mismatch would make the shown count a claim about a different list.
    shown = len(lineage.edges)
    covered = list(lineage.jobs) or list(artifact.requested_jobs)

    content: dict[str, Any] = {
        "project_id": artifact.project_id,
        "resolved": True,
        "source": "scan_field_lineage",
        "scope": {
            "job": artifact.requested_jobs[0] if artifact.requested_jobs else "",
            "group": artifact.requested_group,
            "source_root": params.source_root or "",
            "designs_covered": covered[:MAX_LINEAGE_JOBS_LISTED],
            "designs_covered_total": len(covered),
        },
        "rollup": rollup,
        "page": {
            "offset": lineage.offset,
            "limit": lineage.limit or MAX_LINEAGE_EDGES,
            "shown": shown,
            "total": lineage.total_edges,
            "has_more": lineage.has_more,
            "next_offset": (lineage.offset + shown) if lineage.has_more else None,
        },
        "mappings": [_lineage_edge_content(edge) for edge in lineage.edges],
        "note": (
            "Expressions are the artifact's raw mapping text. Where a mapping is "
            "not resolved the source column is decided at runtime and is not "
            "established by the scan; do not infer one, and never report a "
            "missing mapping as an absence of transformation. "
            "runtime_determined_field_mappings is part of field_mappings_total, "
            "not a remainder to leave out of the answer."
        ),
    }

    targets: list[dict[str, Any]] = [
        job_target(
            name,
            source_root=params.source_root or "",
            intent=intent,
            visual_role=VisualRole.PRIMARY if len(covered) == 1 else VisualRole.SUPPORTING,
        )
        for name in covered[:MAX_LINEAGE_JOBS_LISTED]
    ]
    targets += [
        component_target(
            job_name=job_name,
            component_name=component_name,
            source_root=params.source_root or "",
            intent=intent,
            visual_role=VisualRole.PATH,
        )
        # The components that actually perform these mappings, which is where a
        # "show me" follow-up goes.
        for job_name, component_name in _lineage_mapping_components(lineage)[:MAX_COMPONENT_TARGETS]
    ]
    truncated = bool(
        lineage.has_more
        or len(lineage.target_tables) > MAX_LINEAGE_TARGET_TABLES
        or len(covered) > MAX_LINEAGE_JOBS_LISTED
    )
    return TopologyToolPayload(content=content, targets=targets, truncated=truncated)


def _lineage_rollup(lineage: JobFieldLineage) -> dict[str, Any]:
    """The counts an answer must lead with, including the unresolved share.

    `unresolved` is taken from upstream unless that would contradict
    `entries - resolved`, in which case the derived value wins. A zero
    unresolved count sitting next to entries > resolved is the one arithmetic
    that would silently understate the runtime-determined part of an
    integration, so it is not passed through.
    """
    totals = lineage.totals
    entries = totals.entries or lineage.total_edges
    resolved = totals.resolved
    derived_unresolved = max(entries - resolved, 0)
    unresolved = (
        totals.unresolved if totals.unresolved >= derived_unresolved else derived_unresolved
    )
    confidence = dict(
        sorted(totals.confidence.items(), key=lambda item: (-item[1], item[0]))[
            :MAX_BREAKDOWN_ENTRIES
        ]
    )
    return {
        "field_mappings_total": entries,
        "established_field_mappings": resolved,
        "runtime_determined_field_mappings": unresolved,
        "runtime_determined_share_pct": (round(unresolved * 100 / entries, 1) if entries else 0.0),
        # The `dynamic` confidence bucket specifically, kept separate from
        # `unresolved`: an entry can be unresolved for other reasons, and
        # conflating the two would misreport both.
        "dynamic_confidence_field_mappings": totals.confidence.get("dynamic", 0),
        "confidence_breakdown": confidence,
        "target_tables": list(lineage.target_tables[:MAX_LINEAGE_TARGET_TABLES]),
        "target_tables_total": len(lineage.target_tables),
    }


def _lineage_edge_content(edge: FieldLineageEdge) -> dict[str, Any]:
    return {
        "job": edge.job_name,
        "from": _lineage_field_label(edge.source_table, edge.source_column),
        "from_component": edge.source_component,
        "to": _lineage_field_label(edge.target_table, edge.target_column),
        "to_component": edge.target_component,
        "transformation": edge.transformation_type
        or ("" if edge.resolved else "runtime_determined"),
        "expression": _clipped(edge.expression, limit=MAX_EXPRESSION_CHARS),
        "explanation": _clipped(edge.human_explanation, limit=120),
        "confidence": edge.confidence,
        "resolved": edge.resolved,
        # Joined rather than nested: a leaf list this deep under `content` is
        # replaced by a depth marker in the harness envelope.
        "runtime_refs": _clipped(
            ", ".join(edge.dynamic_refs[:MAX_LINEAGE_DYNAMIC_REFS]), limit=120
        ),
    }


def _lineage_field_label(table: str, column: str) -> str:
    """A `table.column` label, or an explicit statement that there is no source.

    An unresolved mapping carries empty source fields. Rendering that as `"."`
    or as an empty string would read as a mapping from nothing; it is a mapping
    whose source the scan could not establish, and the row has to say so.
    """
    if table and column:
        return f"{table}.{column}"
    if column:
        return column
    if table:
        return table
    return "(not established by the scan)"


def _lineage_mapping_components(lineage: JobFieldLineage) -> list[tuple[str, str]]:
    """Distinct (design, component) pairs performing the mappings on this page."""
    pairs: list[tuple[str, str]] = []
    for edge in lineage.edges:
        pair = (edge.job_name, edge.target_component)
        if edge.job_name and edge.target_component and pair not in pairs:
            pairs.append(pair)
    return pairs


# --- Shared projections ------------------------------------------------------


def _unresolved_payload(
    *,
    artifact: LandscapeArtifact,
    graph: TopologyGraph,
    query: str,
    resolution: EntityResolution,
    intent: NavigationIntent,
) -> TopologyToolPayload:
    """Report every candidate instead of guessing which one was meant.

    Candidates now span nodes AND relationships, and a string that matched both
    lists both. A node match does not outrank an edge match here: whichever of
    them the caller silently dropped would be the one the user had selected.
    """
    nodes = resolution.nodes[:MAX_CANDIDATES]
    edges = resolution.edges[:MAX_CANDIDATES]
    content: dict[str, Any] = {
        "project_id": artifact.project_id,
        "active_view": graph.view.value,
        "resolved": False,
        "reason": "ambiguous" if resolution.total else "not_found",
        "query": query,
        "candidates": [_node_candidate_content(match, graph) for match in nodes]
        + [_relationship_candidate_content(match, graph) for match in edges],
        "matched_kinds": sorted(
            {match.node.entity.value for match in nodes} | ({"relationship"} if edges else set())
        ),
    }
    if nodes and edges:
        content["note"] = (
            f"'{query}' matches both an entity and a relationship in this project. "
            "Ask which was meant, or call again with the id or edge_key of one of "
            "the candidates. Do not answer about one of them as though the other "
            "had not matched."
        )
    return TopologyToolPayload(
        content=content,
        targets=[node_target(match.node, intent=intent) for match in nodes]
        + [
            edge_target(
                match.edge,
                _edge_label(match.edge, graph),
                intent=intent,
                visual_role=VisualRole.PATH,
            )
            for match in edges
        ],
        truncated=resolution.total > MAX_CANDIDATES,
    )


def _relationship_payload(
    *,
    artifact: LandscapeArtifact,
    graph: TopologyGraph,
    match: EdgeMatch,
    query: str,
    intent: NavigationIntent,
) -> TopologyToolPayload:
    """One relationship, when the query named a connection rather than a system.

    Carries both endpoints in full, because the follow-up question is almost
    always about one of them, and because an answer that describes a connection
    without naming what it connects is not checkable.
    """
    edge = match.edge
    content: dict[str, Any] = {
        "project_id": artifact.project_id,
        "active_view": graph.view.value,
        "resolved": True,
        "matched_on": match.match,
        # The one field a caller must read before writing a sentence: it asked
        # for a node-shaped thing by name and got a relationship.
        "matched_entity": "relationship",
        "query": query,
        "relationship": _edge_content(edge, graph),
        "business_facts": _stream_business_facts(edge, artifact.landscape),
        "endpoints": {
            "source": _endpoint_content(edge.source_id, graph),
            "target": _endpoint_content(edge.target_id, graph),
        },
        "note": (
            "This is one connection between two entities, not an entity. Describe "
            "it as the relationship it is, and name both endpoints. Its "
            "implemented_by_designs lists DESIGNS — whole jobs and routes, not "
            "components inside a job; call them jobs or routes when you name them. "
            "get_job_detail accepts those names, and get_field_lineage reports what "
            "data actually moves across it. Say whether the relationship is "
            "established in the words of established_note; do not paste the field "
            "name onto its value. business_facts, when present, is what the "
            "published business topology states about this exchange -- its rules, "
            "conditions, contract width, controls, risks, open questions and "
            "runtime measurements; an omitted key means the overlay states nothing "
            "about it, not that it does not exist."
        ),
    }
    targets = [
        edge_target(edge, _edge_label(edge, graph), intent=intent, visual_role=VisualRole.PRIMARY)
    ]
    # The aggregated pair view exists only in the business graph; see
    # `relationship_target`. Offered before the endpoint nodes because "the view
    # between X and Y" has no other expressible target.
    if graph.view is TopologyView.BUSINESS:
        targets.append(
            relationship_target(
                source_id=edge.source_id,
                target_id=edge.target_id,
                stream_keys=[edge.key],
                label=_edge_label(edge, graph),
                intent=intent,
                visual_role=VisualRole.PATH,
            )
        )
    targets += [
        node_target(node, intent=intent, visual_role=VisualRole.SUPPORTING)
        for node in (graph.node(edge.source_id), graph.node(edge.target_id))
        if node is not None
    ]
    return TopologyToolPayload(content=content, targets=targets)


def _relationship_endpoints_payload(
    *,
    artifact: LandscapeArtifact,
    graph: TopologyGraph,
    match: EdgeMatch,
    query: str,
    intent: NavigationIntent,
) -> TopologyToolPayload:
    """A relationship matched where a traversal root was needed.

    Not an error and not a not_found: the identifier resolved, it just is not a
    thing that has dependencies. Naming the two endpoints lets the next call
    succeed immediately instead of costing an iteration on a failed search.
    """
    edge = match.edge
    endpoints = [
        node
        for node in (graph.node(edge.source_id), graph.node(edge.target_id))
        if node is not None
    ]
    content: dict[str, Any] = {
        "project_id": artifact.project_id,
        "active_view": graph.view.value,
        "resolved": False,
        "reason": "matched_a_relationship_not_an_entity",
        "matched_entity": "relationship",
        "matched_on": match.match,
        "query": query,
        "relationship": _compact_edge_content(edge, graph),
        "candidates": [_node_content(node, graph) for node in endpoints],
        "note": (
            f"'{query}' is a connection, not an entity, so it has no dependency "
            "set of its own. Call again with one of the endpoints below to list "
            "its dependencies, or get_topology_node with the same value to "
            "describe the connection itself."
        ),
    }
    return TopologyToolPayload(
        content=content,
        targets=[
            edge_target(edge, _edge_label(edge, graph), intent=intent, visual_role=VisualRole.PATH),
            *(
                node_target(node, intent=intent, visual_role=VisualRole.SUPPORTING)
                for node in endpoints
            ),
        ],
    )


def _node_candidate_content(match: NodeMatch, graph: TopologyGraph) -> dict[str, Any]:
    return {
        "id": match.node.id,
        "name": match.node.name,
        "kind": match.node.kind,
        "entity": match.node.entity.value,
        "match": match.match,
        "source_roots": list(match.node.source_roots[:MAX_OVERVIEW_ITEMS]),
        "relationship_count": graph.degree(match.node.id),
        "provenance": match.node.provenance.as_content(max_items=MAX_NODE_EVIDENCE),
    }


def _relationship_candidate_content(match: EdgeMatch, graph: TopologyGraph) -> dict[str, Any]:
    """A relationship candidate, shaped so it cannot be mistaken for a node.

    `entity` is `relationship` and the identifier key is `edge_key`, not `id`:
    a candidate list that mixes the two kinds has to make the difference legible
    at a glance, or the model calls back with the wrong sort of identifier.
    """
    return {
        **_compact_edge_content(match.edge, graph),
        "entity": "relationship",
        "match": match.match,
    }


def _endpoint_content(node_id: str, graph: TopologyGraph) -> dict[str, Any]:
    """One end of a relationship, whether or not the active graph draws it.

    An overlay stream can cite a system id the active graph has no node for.
    Saying so is better than omitting the endpoint, which would read as a
    relationship with one end.
    """
    node = graph.node(node_id)
    if node is None:
        return {
            "id": node_id,
            "name": graph.label(node_id),
            "in_active_graph": False,
            "note": "This endpoint id has no node in the graph the canvas is rendering.",
        }
    return {**_node_content(node, graph), "in_active_graph": True}


def _edge_label(edge: TopologyEdgeFact, graph: TopologyGraph) -> str:
    return f"{graph.label(edge.source_id)} -> {graph.label(edge.target_id)}"


@dataclass(frozen=True)
class _Candidate:
    """One search hit, kind-tagged so a mixed candidate list stays honest."""

    entity: str
    tier: int
    content: dict[str, Any]
    target: dict[str, Any]


def _fair_merge(
    buckets: dict[str, list[_Candidate]], *, limit: int
) -> tuple[list[_Candidate], bool]:
    """Round-robin across entity kinds, then present best-tier first.

    A straight tier sort would let 429 job names bury the one system the user
    actually meant, which is the same "silently answered from one source" failure
    this wave exists to fix — one kind monopolising the candidate list hides the
    ambiguity instead of reporting it. Every kind that matched at all is
    represented before any kind gets a second slot.
    """
    total = sum(len(items) for items in buckets.values())
    queues = {key: list(items) for key, items in buckets.items() if items}
    merged: list[_Candidate] = []
    while queues and len(merged) < limit:
        for key in list(queues):
            if len(merged) >= limit:
                break
            merged.append(queues[key].pop(0))
            if not queues[key]:
                del queues[key]
    merged.sort(key=lambda item: -item.tier)
    return merged[:limit], total > limit


def _landscape_candidate_content(match: NodeMatch, graph: TopologyGraph) -> dict[str, Any]:
    return {
        "id": match.node.id,
        "name": match.node.name,
        "kind": match.node.kind,
        "entity": match.node.entity.value,
        "match": match.match,
        "role": match.node.role,
        "description": _clipped(match.node.description),
        "source_roots": list(match.node.source_roots[:MAX_OVERVIEW_ITEMS]),
        "relationship_count": graph.degree(match.node.id),
        # Two systems can share a name; their neighbours and their scanner
        # signals are what actually tells them apart.
        "adjacent": [
            f"{edge.name}: {graph.label(edge.source_id)} -> {graph.label(edge.target_id)}"
            for edge in graph.adjacent(match.node.id, TopologyDirection.BOTH)[:MAX_ADJACENT_LABELS]
        ],
        "provenance": match.node.provenance.as_content(max_items=MAX_NODE_EVIDENCE),
    }


def _design_candidate_content(match: DesignMatch) -> dict[str, Any]:
    """A group, job or component candidate.

    `source_root` is always present, even when empty: on a composite upload it is
    the only thing distinguishing two copies of the same design, and an absent key
    reads as "there is only one" rather than "the scan is flat".
    """
    item = match.item
    base: dict[str, Any] = {
        "name": item.name,
        "entity": match.entity.value,
        "match": match.match,
        "source_root": match.source_root,
    }
    if isinstance(item, DesignGroup):
        return {**base, "job_count": item.job_count, "source_roots": list(item.source_roots[:4])}
    if isinstance(item, DesignJob):
        return {
            **base,
            "kind": item.kind,
            "role": item.role,
            "group": item.group,
            "summary": _clipped(item.summary),
            "direction": item.direction,
            "component_count": item.component_count,
            "external_systems": list(item.external_systems[:MAX_OVERVIEW_ITEMS]),
        }
    component = {
        **base,
        "job": item.job,
        "component_type": item.component_type,
        "role": item.role,
        "fact_types": list(item.fact_types[:MAX_OVERVIEW_ITEMS]),
    }
    # Only stated when the index actually knows it. At project level the manifest
    # names components without saying whether they have a deeper graph, and a
    # False there would read as "this component transforms nothing".
    if item.has_subgraph is not None:
        component["has_mapping_graph"] = item.has_subgraph
    return component


@dataclass(frozen=True)
class _DesignLookup:
    """The design manifest, or an explicit reason it could not be read."""

    index: DesignIndex | None
    reason: str = ""

    def coverage(self) -> dict[str, Any]:
        if self.index is None:
            return {"available": False, "reason": self.reason}
        scope = self.index.component_scope
        coverage: dict[str, Any] = {
            "available": True,
            "groups_indexed": len(self.index.groups),
            "jobs_indexed": len(self.index.jobs),
            "components_indexed": len(self.index.components),
            "component_scope": scope.value,
        }
        if scope is ComponentScope.JOB_GRAPH:
            coverage["component_scope_job"] = self.index.component_job
        elif scope is ComponentScope.MANIFEST_FACTS:
            coverage["component_scope_note"] = (
                "Only components that touch a channel, endpoint or resource are "
                "indexed at project level. Pass job=<design name> to search every "
                "component of one design."
            )
        else:
            coverage["component_scope_note"] = (
                "No component-level facts are published at project level. Pass "
                "job=<design name> to read one design's components."
            )
        return coverage


async def _design_lookup(
    params: FindTopologyNodeInput | GetJobDetailInput | GetComponentDetailInput,
    *,
    provider: TopologyProvider,
    access_token: str | None,
) -> _DesignLookup:
    """Read the project design manifest, degrading rather than failing the call.

    A project can have a landscape rollup and no readable design manifest (an old
    scan, a partial publish). The landscape half of an answer is still valid then,
    so the manifest's absence is reported as coverage rather than raised — but it
    is always reported, never omitted, or the model would present a partial search
    as exhaustive.

    Timeouts and transport failures are deliberately NOT caught: those are failures
    of the whole read, not a coverage gap.
    """
    try:
        artifact = await provider.get_project_graph(
            project_id=params.project_id, access_token=access_token
        )
    except TopologyArtifactUnavailableError:
        return _DesignLookup(
            index=None,
            reason=(
                "This project has no published design manifest, so its groups, "
                "jobs and components cannot be searched."
            ),
        )
    except TopologyPayloadError:
        return _DesignLookup(
            index=None,
            reason="This project's design manifest is not readable and needs a rescan.",
        )

    scoped_job = params.job if isinstance(params, FindTopologyNodeInput) else None
    job_graph: JobGraph | None = None
    if scoped_job:
        job_graph, _note = await _job_graph(
            provider,
            project_id=params.project_id,
            job_name=scoped_job,
            access_token=access_token,
        )
    return _DesignLookup(index=build_design_index(artifact.graph, job_graph=job_graph))


async def _job_graph(
    provider: TopologyProvider,
    *,
    project_id: str,
    job_name: str,
    access_token: str | None,
) -> tuple[JobGraph | None, str]:
    """DEEP read of one job's component graph, or a reason it is not there."""
    try:
        artifact = await provider.get_job_graph(
            project_id=project_id, job_name=job_name, access_token=access_token
        )
    except (TopologyDesignUnavailableError, TopologyArtifactUnavailableError) as exc:
        return None, exc.safe_message
    except TopologyPayloadError as exc:
        return None, exc.safe_message
    return artifact.graph, ""


def _no_design_manifest_payload(project_id: str, design: _DesignLookup) -> TopologyToolPayload:
    """Say the design layer is absent. Never fall back to the landscape rollup.

    The rollup has no job or component nodes, so answering a job question from it
    would mean answering from a source that does not contain the subject.
    """
    return TopologyToolPayload(
        content={
            "project_id": project_id,
            "resolved": False,
            "reason": "design_manifest_unavailable",
            "note": design.reason,
            "design_coverage": design.coverage(),
        },
        targets=[],
    )


def _ambiguous_design_payload(
    *,
    project_id: str,
    query: str,
    entity: DesignEntity,
    items: list[DesignJob] | list[DesignComponent],
    intent: NavigationIntent,
) -> TopologyToolPayload:
    """Report every candidate design, with its kind and its source root."""
    # Every candidate is in the resolver's winning tier, which for a real name is
    # the exact-name tier; the label reported alongside it says so.
    matches = [DesignMatch(entity=entity, item=item, tier=MatchTier.EXACT_NAME) for item in items]
    return TopologyToolPayload(
        content={
            "project_id": project_id,
            "resolved": False,
            "reason": "ambiguous" if items else "not_found",
            "query": query,
            "entity": entity.value,
            "candidates": [_design_candidate_content(match) for match in matches[:MAX_CANDIDATES]],
        },
        targets=[design_target(match, intent=intent) for match in matches[:MAX_CANDIDATES]],
        truncated=len(matches) > MAX_CANDIDATES,
    )


def _job_content(job: DesignJob) -> dict[str, Any]:
    return {
        "name": job.name,
        "kind": job.kind,
        "role": job.role,
        "group": job.group,
        "source_root": job.source_root,
        "direction": job.direction,
        "summary": _clipped(job.summary),
        "declared_component_count": job.component_count,
        "component_types": list(job.component_types[:MAX_BREAKDOWN_ENTRIES]),
        "external_systems": list(job.external_systems[:MAX_OVERVIEW_ITEMS]),
    }


#: Listing order for a job's components. A real job has up to ~250 of them and
#: only a dozen are shown, so the shown ones are the ones a follow-up question
#: is about: what can be drilled into, then what touches the outside world, then
#: control flow, and plumbing last.
_COMPONENT_ROLE_ORDER = {"input": 1, "output": 2, "transform": 3, "orchestration": 4, "logging": 5}


def _component_order(component: JobGraphComponent) -> tuple[int, int, str]:
    return (
        0 if component.has_subgraph else 1,
        _COMPONENT_ROLE_ORDER.get(component.role, 9),
        component.unique_name.casefold(),
    )


def _component_summary(component: JobGraphComponent) -> dict[str, Any]:
    return {
        "name": component.unique_name,
        "label": component.display_label,
        "type": component.component_type,
        "role": component.role,
        "has_mapping_graph": component.has_subgraph,
        "runs_job": component.subjob_target,
        "connection_ref": component.connection_ref,
        # Whether the scan captured any configured setting for this component.
        # A summary without it says what a component IS and never whether it
        # holds a decision, which is what a caller sweeping a design is looking
        # for; with it, one listing call says where to look next.
        "has_settings": component.has_settings,
        "description": _clipped(component.description),
    }


def _batch_param_budget(content: dict[str, Any]) -> int:
    """Room left under the result ceiling for one page of component settings.

    Measured in serialized characters, the same unit the ceiling is expressed
    in, because a budget counted in anything else is not comparable to the thing
    it is protecting: raw field lengths ignore JSON quoting and separators and
    under-count a parameter block by roughly half.
    """
    used = len(json.dumps(content, default=str))
    room = settings.runtime_tool_result_max_chars - used - JOB_PARAM_BUDGET_RESERVE
    return max(0, min(MAX_JOB_PARAM_CHARS, room))


def _component_listing(
    components: Sequence[JobGraphComponent], *, budget: int
) -> tuple[list[dict[str, Any]], int]:
    """One page of components with their settings, and how many did not fit.

    The whole page shares one budget rather than each component getting a fixed
    slice: on real data four of five components carry no settings at all, so a
    fixed per-component slice would either starve the rich components or reserve
    room for the empty ones. Budget is spent in page order -- `_component_order`,
    the same total order the paging uses -- so the result is deterministic for a
    given page.

    A component whose settings do not fit still appears with its summary and is
    counted in the returned total. It is never dropped and never silently
    reduced: "the settings of three components on this page were elided" is a
    fact the caller can act on, a component that vanished is not.
    """
    listed: list[dict[str, Any]] = []
    remaining = budget
    omitted = 0
    for item in components:
        summary = _component_summary(item)
        if not item.has_settings:
            listed.append(summary)
            continue
        parameters = (
            _component_parameters(item, budget=min(MAX_COMPONENT_PARAM_CHARS, remaining))
            if remaining > 0
            else {}
        )
        cost = len(json.dumps(parameters, default=str)) if parameters else 0
        if not _has_settings_content(parameters) or cost > remaining:
            omitted += 1
            listed.append(summary)
            continue
        remaining -= cost
        listed.append({**summary, **parameters})
    return listed, omitted


def _has_settings_content(parameters: dict[str, Any]) -> bool:
    """Whether a rendered block actually carries a setting, not just its keys."""
    return bool(parameters.get("parameters") or parameters.get("parameter_tables"))


def _component_parameters(component: JobGraphComponent, *, budget: int) -> dict[str, Any]:
    """A component's configured settings, scalar and row-structured, in `budget`.

    Two shapes because upstream has two: scalar params come back as a name ->
    value map, and TABLE params as rows. Both are already allow-listed and
    credential-filtered upstream; they are re-clipped here so one long parameter
    cannot consume the whole result budget.

    The rows are rendered to one bounded STRING per row rather than nested as
    dicts. Not cosmetic: the harness replaces any container past six levels with
    a depth marker, and a batched read nests data -> content -> components ->
    component -> parameter_tables -> rows, which puts a row's own fields exactly
    one level too deep. Flattening the leaf is the same treatment
    `mapping_tables.columns` already gets for the same reason.

    `budget` is a character allowance, so a batch caller can hold a whole page
    inside one ceiling. Elided tables and rows are always counted in the notes:
    a table truncated in silence reads as a complete short table, and "there are
    three more routing rules" is the difference between a partial answer and a
    wrong one.
    """
    parameters = {
        key: _clipped(str(value), limit=120)
        for key, value in list(component.param_summary.items())[:MAX_PARAM_ENTRIES]
    }
    content: dict[str, Any] = {"parameters": parameters}
    if not component.param_tables:
        return content

    spent = sum(len(key) + len(str(value)) for key, value in parameters.items())
    tables: dict[str, list[str]] = {}
    notes: dict[str, str] = {}
    tables_omitted = 0
    for table in component.param_tables:
        if len(tables) >= MAX_PARAM_TABLES or spent >= budget:
            tables_omitted += 1
            continue
        rendered: list[str] = []
        for row in table.rows[:MAX_PARAM_TABLE_ROWS]:
            line = _render_param_row(row)
            if not line or spent + len(line) > budget:
                break
            rendered.append(line)
            spent += len(line)
        if not rendered and not table.rows_redacted:
            tables_omitted += 1
            continue
        tables[table.name] = rendered
        note = _param_table_note(table, shown=len(rendered))
        if note:
            notes[table.name] = note
    if tables:
        content["parameter_tables"] = tables
    if notes:
        # A sibling map rather than a field inside each table, for the same
        # depth reason the rows are strings.
        content["parameter_table_notes"] = notes
    if tables_omitted:
        content["parameter_tables_omitted"] = tables_omitted
    return content


def _render_param_row(row: dict[str, Any]) -> str:
    """One table row as `COLUMN=value | COLUMN=value`, bounded."""
    cells = [f"{key}={value}" for key, value in row.items() if value not in (None, "")]
    return _clipped(" | ".join(cells), limit=MAX_PARAM_ROW_CHARS)


def _param_table_note(table: JobGraphParamTable, *, shown: int) -> str:
    """What this table did not show, or "" when it showed everything."""
    if shown == table.row_count and not table.rows_redacted:
        return ""
    parts = [f"{shown} of {table.row_count} rows shown"]
    if table.rows_redacted:
        parts.append(f"{table.rows_redacted} withheld by the credential filter")
    return "; ".join(parts)


def _component_content(component: JobGraphComponent) -> dict[str, Any]:
    return {
        **_component_summary(component),
        "type_explanation": _clipped(component.type_explanation),
        "has_custom_code": component.has_code,
        **_component_parameters(component, budget=MAX_COMPONENT_DETAIL_PARAM_CHARS),
    }


def _connection_content(edge: JobGraphConnection) -> dict[str, Any]:
    return {
        "name": edge.name,
        "source": edge.source,
        "target": edge.target,
        "connector": edge.connector_name,
        "control_flow": edge.is_control_flow,
        "explanation": _clipped(edge.explanation, limit=120),
    }


#: Connector names that carry an error path rather than a happy path.
_ERROR_CONNECTORS = ("ERROR", "RUN_IF")
#: Component types that terminate or flag a run.
_FAILURE_TYPES = frozenset({"tDie", "tWarn", "tAssertCatcher", "tLogCatcher"})


def _health_facts(
    components: list[JobGraphComponent], edges: list[JobGraphConnection]
) -> dict[str, Any]:
    """The design facts that bear on "what breaks if this fails".

    Structure only. Whether this job HAS failed is a runtime question and belongs
    to the runtime tools; presenting either as the other is the source-labelling
    defect this wave is closing.
    """
    return {
        "logging_components": [item.unique_name for item in components if item.role == "logging"][
            :MAX_OVERVIEW_ITEMS
        ],
        "failure_handling_components": [
            item.unique_name for item in components if item.component_type in _FAILURE_TYPES
        ][:MAX_OVERVIEW_ITEMS],
        "error_path_count": sum(
            1
            for edge in edges
            if any(token in (edge.connector_name or "").upper() for token in _ERROR_CONNECTORS)
        ),
        "control_flow_count": sum(1 for edge in edges if edge.is_control_flow),
        "data_flow_count": sum(1 for edge in edges if not edge.is_control_flow),
        "child_jobs": [item.subjob_target for item in components if item.subjob_target][
            :MAX_OVERVIEW_ITEMS
        ],
        "note": "Design-time structure only; no run has been observed here.",
    }


def _resolve_component(job_graph: JobGraph, query: str) -> list[JobGraphComponent]:
    """Best-tier component matches within one job, by unique name or label."""
    needle = query.strip().casefold()
    tiered: dict[int, list[JobGraphComponent]] = {}
    for node in job_graph.nodes:
        candidates = (node.unique_name, node.display_label)
        tier = max(
            (
                int(found)
                for name in candidates
                if name and (found := name_match_tier(name, needle))
            ),
            default=0,
        )
        if tier:
            tiered.setdefault(tier, []).append(node)
    for tier in sorted(tiered, reverse=True):
        return sorted(tiered[tier], key=lambda item: item.unique_name.casefold())
    return []


async def _mapping_content(
    provider: TopologyProvider,
    *,
    project_id: str,
    job_name: str,
    component: JobGraphComponent,
    access_token: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The component's mapping graph, or an explicit statement that it has none."""
    if not component.has_subgraph:
        return (
            {
                "mapping_available": False,
                "mapping_reason": "no_deeper_graph",
                "mapping_note": (
                    f"'{component.unique_name}' is a "
                    f"{component.component_type or 'component'} and publishes no "
                    "column-level mapping graph. Its job-level wiring above is the "
                    "whole structure the artifact carries for it."
                ),
            },
            None,
        )
    try:
        artifact = await provider.get_component_graph(
            project_id=project_id,
            job_name=job_name,
            component_name=component.unique_name,
            access_token=access_token,
        )
    except TopologyMappingUnavailableError as exc:
        return (
            {
                "mapping_available": False,
                "mapping_reason": "no_deeper_graph",
                "mapping_note": exc.safe_message,
            },
            None,
        )
    except (TopologyDesignUnavailableError, TopologyPayloadError) as exc:
        return (
            {
                "mapping_available": False,
                "mapping_reason": "unreadable",
                "mapping_note": exc.safe_message,
            },
            None,
        )
    return _mapping_graph_content(artifact.graph), _mapping_visual_detail(artifact.graph)


def _mapping_graph_content(graph: ComponentGraph) -> dict[str, Any]:
    tables = [
        *(("input", table) for table in graph.input_tables),
        ("var", graph.var_table),
        *(("output", table) for table in graph.output_tables),
    ]
    expressions = graph.edges
    unresolved = sum(1 for edge in expressions if not edge.resolved_refs)
    return {
        "mapping_available": True,
        "mapping_table_count": len(tables),
        "mapping_tables": [
            _mapping_table_content(role, table) for role, table in tables[:MAX_MAPPING_TABLES]
        ],
        "mapping_expression_count": len(expressions),
        "mapping_expressions": [
            {
                "target": f"{edge.target_table}.{edge.target_column}",
                "expression": _clipped(edge.expression, limit=MAX_EXPRESSION_CHARS),
                "confidence": edge.confidence,
                "resolved_refs": list(edge.resolved_refs[:MAX_MAPPING_REFS]),
            }
            for edge in expressions[:MAX_MAPPING_EXPRESSIONS]
        ],
        "mapping_truncated": len(tables) > MAX_MAPPING_TABLES
        or len(expressions) > MAX_MAPPING_EXPRESSIONS,
        # The upstream artifact carries the raw expression and no resolved source
        # column for most rows. Saying which column feeds which would be an
        # invention, so the count is reported instead.
        "mapping_unresolved_expression_count": unresolved,
        "mapping_note": (
            "Expressions are the artifact's raw mapping text. Where resolved_refs is "
            "empty the source column is not established by the scan; do not infer one."
        ),
    }


def _mapping_visual_detail(graph: ComponentGraph) -> dict[str, Any]:
    """Bounded semantic emphasis derived only from the component graph.

    These identities are not navigation levels and never come from model
    arguments. The topology surface maps them onto tables, column rows and
    mapping edges only after it has loaded and validated the same component
    graph.
    """

    all_tables = [*graph.input_tables, graph.var_table, *graph.output_tables]

    def token(value: str) -> str:
        return value.strip()[:200]

    tables: list[str] = []
    for table in all_tables:
        name = token(table.table_name)
        if name and name not in tables:
            tables.append(name)
        if len(tables) >= MAX_MAPPING_TABLES:
            break

    columns: list[dict[str, str]] = []
    seen_columns: set[tuple[str, str]] = set()

    def add_column(table_name: str, column_name: str) -> None:
        table = token(table_name)
        column = token(column_name)
        key = (table, column)
        if (
            not table
            or not column
            or key in seen_columns
            or len(columns) >= MAX_MAPPING_EXPRESSIONS
        ):
            return
        seen_columns.add(key)
        columns.append({"table": table, "column": column})

    # Mapping targets first: these are the columns an explanation most directly
    # talks about. Fill any remaining budget from declared table schemas.
    for edge in graph.edges[:MAX_MAPPING_EXPRESSIONS]:
        add_column(edge.target_table, edge.target_column)
    for table in all_tables:
        for column in table.columns:
            add_column(table.table_name, column)

    edges = []
    for edge in graph.edges[:MAX_MAPPING_EXPRESSIONS]:
        target_table = token(edge.target_table)
        target_column = token(edge.target_column)
        if not target_table or not target_column:
            continue
        edges.append(
            {
                "target_table": target_table,
                "target_column": target_column,
            }
        )
    return {"kind": "mapping", "tables": tables, "columns": columns, "edges": edges}


def _mapping_table_content(role: str, table: ComponentGraphTable) -> dict[str, Any]:
    # Columns are joined into one string rather than nested as a list: at this
    # depth the harness envelope replaces a leaf list with a depth marker.
    return {
        "role": role,
        "table": table.table_name,
        "fed_by": table.source_component,
        "column_count": len(table.columns),
        "columns": _clipped(", ".join(table.columns)),
    }


def _system_pairs(
    node: TopologyNode, edges: list[TopologyEdgeFact], graph: TopologyGraph
) -> list[tuple[tuple[str, str], list[str]]]:
    """Directed system pairs touching a node, with their constituent stream keys.

    Only in the business view and only for systems: the canvas aggregates one
    edge per directed system pair in `businessLandscapeToFlow` and nowhere else,
    so a pair target built from the scanner view would navigate to a node the
    diagram does not draw.
    """
    if graph.view is not TopologyView.BUSINESS or node.entity is not NodeEntity.SYSTEM:
        return []
    pairs: dict[tuple[str, str], list[str]] = {}
    for edge in edges:
        pairs.setdefault((edge.source_id, edge.target_id), []).append(edge.key)
    ordered = sorted(pairs.items(), key=lambda item: (-len(item[1]), item[0]))
    return ordered[:MAX_PAIR_TARGETS]


def _node_content(node: TopologyNode, graph: TopologyGraph) -> dict[str, Any]:
    return {
        "id": node.id,
        "name": node.name,
        "kind": node.kind,
        "entity": node.entity.value,
        "role": node.role,
        "description": _clipped(node.description),
        "aliases": list(node.aliases[:MAX_OVERVIEW_ITEMS]),
        "source_roots": list(node.source_roots[:MAX_OVERVIEW_ITEMS]),
        "artifact_count": node.artifact_count,
        "relationship_count": graph.degree(node.id),
        "provenance": node.provenance.as_content(max_items=MAX_NODE_EVIDENCE),
    }


def _node_evidence_content(node: TopologyNode, landscape: ProjectLandscape) -> dict[str, Any]:
    """Evidence behind a node, keeping scanner facts and overlay claims apart."""
    evidence: dict[str, Any] = {
        "naming": node.provenance.naming.value,
        "scanner_signals": list(node.provenance.scanner_signals[:MAX_NODE_EVIDENCE]),
        "overlay_evidence_ids": list(node.provenance.evidence_ids[:MAX_NODE_EVIDENCE]),
    }
    if node.provenance.naming is not Naming.BUSINESS_OVERLAY:
        return evidence

    # The artifact publishes no identity bridge from an overlay system back to
    # the scanner entity it describes, so this is reported as a name match and
    # labelled as one. The scanner signals it exposes (connection names) are
    # often the only way to tell two same-named systems apart.
    name = node.name.casefold()
    named_matches = [
        {
            "id": system.id,
            "kind": system.kind,
            "source_roots": system.source_roots[:MAX_OVERVIEW_ITEMS],
            "scanner_signals": sorted(
                {f"{item.signal_type}:{item.label}" for item in system.evidence if item.label}
            )[:MAX_NODE_EVIDENCE],
        }
        for system in landscape.systems
        if (system.name or "").casefold() == name
    ]
    if named_matches:
        evidence["name_matched_scanner_systems"] = named_matches[:MAX_NAME_MATCHED_SCANNER_ENTITIES]
        evidence["name_match_note"] = (
            "Matched by name only. The published artifact does not link overlay "
            "systems to scanner systems by id, so this is supporting evidence, "
            "not an established identity."
        )
    return evidence


#: How a relationship came to be asserted, as a sentence rather than a token.
#:
#: The bare token used to be published as `claim_status`, and a reader was told
#: "this stream has confirmed claim status" -- the field name pasted onto its own
#: value. A token carries no grammar, so a caller has to invent the surrounding
#: words and invents them wrong. The sentence is the paraphrasable form; the token
#: stays alongside it under a name that reads as a fact rather than as a schema
#: field, for callers that need to branch on it.
_ESTABLISHMENT_NOTES: dict[str, str] = {
    "confirmed": "The scan read this relationship directly out of the code.",
    "inferred": (
        "The scan did not read this relationship out of the code. It was inferred "
        "from the designs that carry this traffic, so what moves across it is not "
        "established."
    ),
    "observed": "This is wiring the scanner observed directly in the designs.",
}
_ESTABLISHMENT_UNKNOWN = "The artifact does not say how this relationship was established."


def _establishment_content(claim_status: str) -> dict[str, Any]:
    normalized = (claim_status or "").strip().casefold()
    return {
        "established": normalized or "unknown",
        "established_note": _ESTABLISHMENT_NOTES.get(normalized, _ESTABLISHMENT_UNKNOWN),
    }


def _edge_content(edge: TopologyEdgeFact, graph: TopologyGraph) -> dict[str, Any]:
    return {
        "edge_key": edge.key,
        "name": edge.name,
        "source": {"id": edge.source_id, "name": graph.label(edge.source_id)},
        "target": {"id": edge.target_id, "name": graph.label(edge.target_id)},
        "dependency_type": edge.dependency_type,
        "mechanism": edge.mechanism,
        "reliability": edge.reliability,
        "confidence": edge.confidence,
        "evidence_count": edge.evidence_count,
        "naming": edge.naming.value,
        **_establishment_content(edge.claim_status),
        "business_objects": list(edge.business_objects[:MAX_OVERVIEW_ITEMS]),
        "protocols": list(edge.protocols[:MAX_OVERVIEW_ITEMS]),
        "executed_by": list(edge.integrations[:MAX_OVERVIEW_ITEMS]),
        # Named for WHAT these are, not for where they came from. As
        # `scanner_provenance` they were an untyped list of strings, and a caller
        # describing them had to guess their kind -- it guessed "components", which
        # in this domain means a box inside a design and sent the reader looking in
        # the wrong place. These are designs: whole jobs and routes, and exactly
        # what `get_job_detail` accepts.
        "implemented_by_designs": list(edge.scanner_provenance[:MAX_EDGE_PROVENANCE]),
        "implemented_by_designs_total": len(edge.scanner_provenance),
    }


def _compact_edge_content(edge: TopologyEdgeFact, graph: TopologyGraph) -> dict[str, Any]:
    """Enough to describe a dependency; per-edge evidence is get_topology_node's job."""
    return {
        "edge_key": edge.key,
        "name": edge.name,
        "source": {"id": edge.source_id, "name": graph.label(edge.source_id)},
        "target": {"id": edge.target_id, "name": graph.label(edge.target_id)},
        "dependency_type": edge.dependency_type,
        "mechanism": edge.mechanism,
        "naming": edge.naming.value,
        **_establishment_content(edge.claim_status),
        "evidence_count": edge.evidence_count,
    }


def _topology_state_content(landscape: ProjectLandscape) -> dict[str, Any]:
    state = landscape.topology_state
    if state is None:
        return {
            "available": False,
            "validated": False,
            "stale": False,
            "source": "none",
            "message": "No business topology state was published with this artifact.",
        }
    return {
        "available": state.available,
        "validated": state.validated,
        "stale": state.stale,
        "source": state.source,
        "message": _clipped(state.message),
    }


def _business_overlay_content(business: BusinessTopology | None) -> dict[str, Any] | None:
    if business is None:
        return None
    return {
        "schema_version": business.schema_version,
        "scan_fingerprint": business.scan_fingerprint,
        "generated_by": str(business.metadata.get("generated_by") or ""),
        "systems": len(business.systems),
        "systems_by_kind": _counted(item.kind or "unknown" for item in business.systems),
        "systems_by_establishment": _counted(item.status or "unknown" for item in business.systems),
        "data_streams": len(business.data_streams),
        "streams_by_establishment": _counted(
            item.status or "unknown" for item in business.data_streams
        ),
        "business_objects": len(business.business_objects),
        "controls": len(business.controls),
        "risks": len(business.risks),
        "unknowns": len(business.unknowns),
        "conflicts": len(business.conflicts),
        "transformations": len(business.transformations),
        "transformations_by_kind": _counted(
            item.kind or "unknown" for item in business.transformations
        ),
        # A rule is a decision the integration layer takes for the business. A
        # count of zero means none were published, never that none exist.
        "business_rules": len(business.business_rules),
        "business_rules_by_kind": _counted(
            item.kind or "unknown" for item in business.business_rules
        ),
        "streams_by_lifecycle": _counted(
            item.lifecycle or "unknown" for item in business.data_streams
        ),
        "integration_solutions": [
            {"id": item.id, "name": item.name, "role": item.role, "platform": item.platform}
            for item in business.integration_solutions[:MAX_OVERVIEW_ITEMS]
        ],
        "coverage": {
            "cited_artifacts": business.coverage.cited_artifacts,
            "total_artifacts": business.coverage.total_artifacts,
            "coverage_ratio": business.coverage.coverage_ratio,
            "grounded_technical_relationships": (
                business.coverage.grounded_technical_relationships
            ),
            "technical_relationships": business.coverage.technical_relationships,
            "notes": [_clipped(note) for note in business.coverage.notes[:MAX_OVERVIEW_ITEMS]],
        },
    }


def _stream_business_facts(
    edge: TopologyEdgeFact, landscape: ProjectLandscape
) -> dict[str, Any] | None:
    """What the published overlay says about ONE stream, bounded.

    The graph edge carries identity and wiring only. Everything the overlay
    states about how the exchange behaves -- its structured facts, the rules it
    applies, what guards it, what is still open and what runtime measured --
    lives beside the stream in the overlay's own collections. Without this block
    the answer to "which business rules does this exchange apply" was the
    overview's rule COUNT, which reads as "none" to the reader.

    None for a scanner edge or when no active overlay is published; absent
    facts are omitted, never rendered as "not established".
    """
    business = active_business_topology(landscape)
    if business is None or edge.dependency_type != "business_data_stream":
        return None
    stream = next(
        (item for item in business.data_streams if business_stream_edge_key(item.id) == edge.key),
        None,
    )
    if stream is None:
        return None

    # The SAME linking rules as the canvas (`streamBusinessLayer` in
    # frontend/lib/architectureViewModel.ts): a rule belongs to a stream through
    # its stream_ids OR through one of the stream's transformations; a conflict
    # through the stream's conflict_ids OR its subject_refs (conflicts carry no
    # stream_ids). Diverging here made the assistant deny a rule or conflict
    # the canvas was showing.
    def linked(items: Sequence[Any], ids: Sequence[str]) -> list[Any]:
        wanted = set(ids)
        return [item for item in items if item.id in wanted or stream.id in item.stream_ids]

    transformations = [
        item for item in business.transformations if stream.id in item.stream_ids
    ]
    transformation_ids = {item.id for item in transformations}
    rules = [
        item
        for item in business.business_rules
        if stream.id in item.stream_ids
        or any(ref in transformation_ids for ref in item.transformation_ids)
    ]
    conflict_ids = set(stream.conflict_ids)
    conflicts = [
        item
        for item in business.conflicts
        if item.id in conflict_ids or stream.id in item.subject_refs
    ]

    facts: dict[str, Any] = {"stream_id": stream.id}
    for field in ("mechanism", "generation", "key_form", "verdict"):
        value = getattr(stream, field)
        if value:
            facts[field] = _clipped(value)
    for field in ("direction_pattern", "lifecycle", "requiredness"):
        value = getattr(stream, field)
        if value and value not in {"unknown", "not_established"}:
            facts[field] = value
    if stream.business_summary:
        facts["business_summary"] = _clipped(stream.business_summary)
    if stream.contract_width is not None:
        facts["contract_width"] = stream.contract_width.model_dump()
    if stream.conditions:
        facts["conditions"] = [
            {"kind": item.kind, "statement": _clipped(item.statement, limit=MAX_EXPRESSION_CHARS)}
            for item in stream.conditions[:MAX_OVERVIEW_ITEMS]
        ]
    if stream.deployment is not None:
        facts["deployment"] = stream.deployment.model_dump()
    if stream.document_refs:
        facts["document_refs"] = list(stream.document_refs[:MAX_OVERVIEW_ITEMS])

    if rules:
        facts["business_rules"] = [
            {
                "name": item.name,
                "kind": item.kind,
                "statement": _clipped(item.statement),
                "owner": item.owner,
                "owner_question": _clipped(item.owner_question),
                "status": item.status,
            }
            for item in rules[:MAX_OVERVIEW_ITEMS]
        ]
    if transformations:
        facts["transformations"] = [
            {
                "name": item.name,
                "kind": item.kind,
                "target_field": item.target_field,
                "mapping_count": len(item.mappings),
                "mappings": [
                    {
                        "source": mapping.source,
                        "target": mapping.target,
                        "expression": _clipped(mapping.expression, limit=MAX_EXPRESSION_CHARS),
                        "reaches_target": mapping.reaches_target,
                    }
                    for mapping in item.mappings[:MAX_MAPPING_EXPRESSIONS]
                ],
            }
            for item in transformations[:MAX_OVERVIEW_ITEMS]
        ]
    controls = linked(business.controls, stream.control_ids)
    if controls:
        facts["controls"] = [
            {"name": item.name, "control_type": item.control_type} for item in controls[:MAX_OVERVIEW_ITEMS]
        ]
    risks = linked(business.risks, stream.risk_ids)
    if risks:
        facts["risks"] = [
            {"name": item.name, "severity": item.severity, "description": _clipped(item.description)}
            for item in risks[:MAX_OVERVIEW_ITEMS]
        ]
    unknowns = linked(business.unknowns, stream.unknown_ids)
    if unknowns:
        facts["open_questions"] = [
            _clipped(item.question or item.name) for item in unknowns[:MAX_OVERVIEW_ITEMS]
        ]
    if conflicts:
        facts["conflicts"] = [
            {
                "name": item.name,
                "conflict_type": item.conflict_type,
                "resolution": _clipped(item.resolution),
            }
            for item in conflicts[:MAX_OVERVIEW_ITEMS]
        ]
    observations = linked(business.runtime_observations, stream.runtime_observation_ids)
    if observations:
        facts["runtime_observations"] = [
            {
                "kind": item.kind,
                "observed_behavior": _clipped(item.observed_behavior),
                "window": item.observation_window,
                "source": item.source,
                "metrics": [
                    {
                        "label": metric.label,
                        "value": metric.value,
                        "unit": metric.unit,
                        "window": metric.window,
                    }
                    for metric in item.metrics[:MAX_OVERVIEW_ITEMS]
                ],
            }
            for item in observations[:MAX_OVERVIEW_ITEMS]
        ]
    return facts


def _by_severity(landscape: ProjectLandscape) -> list[LandscapeDiagnostic]:
    order = {"critical": 0, "error": 1, "high": 1, "warning": 2, "medium": 3, "info": 4, "low": 5}
    return sorted(
        landscape.diagnostics,
        key=lambda item: (order.get((item.severity or "info").casefold(), 6), item.kind),
    )


def _counted(values: Iterable[str]) -> dict[str, int]:
    """Descending count breakdown, capped so a wide estate cannot flood output."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return dict(ordered[:MAX_BREAKDOWN_ENTRIES])


def _clipped(text: str, *, limit: int = MAX_TEXT_CHARS) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."
