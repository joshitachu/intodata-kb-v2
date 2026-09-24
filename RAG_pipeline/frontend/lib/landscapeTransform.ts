import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";
import type {
  LandscapeApplication,
  LandscapeDiagnostic,
  LandscapeEdge,
  LandscapeProvenance,
  LandscapeSystem,
  ProjectLandscapeResponse,
  BusinessDataStream,
  BusinessObject,
  BusinessSystem,
} from "@/types/landscape";
import { streamDeliveryPath } from "@/lib/architectureViewModel";

export type LandscapeNodeData =
  | { entityType: "application"; application: LandscapeApplication }
  | { entityType: "system"; system: LandscapeSystem }
  | { entityType: "businessSystem"; system: BusinessSystem };

export type TopologyEdge = LandscapeEdge & {
  businessStream?: BusinessDataStream;
};

/** xyflow's `Edge<T>` requires `T extends Record<string, unknown>`; both
 * edge-data shapes below carry an explicit index signature so the union
 * satisfies that constraint. */
export interface LandscapeEdgeSingleData extends Record<string, unknown> {
  landscapeEdge: TopologyEdge;
}

/** L1 business-landscape edges are aggregated one-per-directed-system-pair
 * (see `businessLandscapeToFlow`); this carries the constituent stream keys
 * so a click can open a chooser over the individual flows without inventing
 * a synthetic combined `TopologyEdge`. `findLandscapeEdge` still resolves
 * each key to its own single-stream `TopologyEdge` for the L2 detail view. */
export interface AggregatedRelationshipEdgeData extends Record<string, unknown> {
  streamKeys: string[];
  pair: { source: string; target: string };
}

export type LandscapeEdgeData = LandscapeEdgeSingleData | AggregatedRelationshipEdgeData;

/** Stable id for an aggregated system-pair edge. Distinct from
 * `landscapeEdgeKey` output so click handlers can tell them apart by prefix. */
export function aggregatedRelationshipId(source: string, target: string): string {
  return `pair:${source}->${target}`;
}

export function isAggregatedRelationshipEdge(data: LandscapeEdgeData | undefined): data is AggregatedRelationshipEdgeData {
  return Boolean(data && "streamKeys" in data);
}

export interface LandscapeFilters {
  query?: string;
  dependencyTypes?: Set<string>;
  mechanisms?: Set<string>;
  showSystems?: boolean;
  diagnosticSeverities?: Set<string>;
  /** Business view only. Falsy/undefined (default) hides observability/auth
   * "plumbing" streams (logging, monitoring, LDAP); true reveals them. */
  showInfrastructure?: boolean;
}

export interface LandscapeFlow {
  nodes: Node<LandscapeNodeData>[];
  edges: Edge<LandscapeEdgeData>[];
  diagnostics: LandscapeDiagnostic[];
}

/** Stable identity for an aggregated fact; labels and array position are never identity. */
export function landscapeEdgeKey(edge: LandscapeEdge): string {
  const streamId = (edge as TopologyEdge).businessStream?.id;
  if (streamId) return `business-stream\u001f${streamId}`;
  return [
    edge.source_kind,
    edge.source,
    edge.target_kind,
    edge.target,
    edge.dependency_type,
    edge.mechanism,
    edge.direction,
  ].join("\u001f");
}

export function parseLandscapeEdgeKey(key: string): string[] {
  return key.split("\u001f");
}

/** Resolve a stable graph id to the human label carried by the scan result. */
export function landscapeEntityLabel(
  landscape: ProjectLandscapeResponse,
  entityId: string,
): string {
  const application = landscape.applications.find((item) => item.id === entityId);
  if (application) return application.label || application.source_root;
  const system = landscape.systems.find((item) => item.id === entityId);
  if (system) return system.name;
  return landscape.business_topology?.systems.find((item) => item.id === entityId)?.name
    || landscape.business_topology?.integration_solutions.find((item) => item.id === entityId)?.name
    || entityId;
}

export function topologyTerm(value: string): string {
  const known: Record<string, string> = {
    control_flow: "Control flow",
    data: "Data flow",
    integration: "Integration",
    config: "Configuration",
    direct: "Direct call",
    "direct-vm": "Direct in-memory call",
    jms: "Message queue",
    db: "Database",
    buffered: "Buffered / retryable",
    synchronous: "Direct / synchronous",
    certain: "Confirmed",
    likely: "Likely",
    inferred: "Inferred",
    unknown: "Not established",
  };
  return known[value.toLocaleLowerCase()] ?? value.replaceAll("_", " ");
}

const APPLICATION_SIZE = { width: 230, height: 126 };
const SYSTEM_SIZE = { width: 205, height: 104 };

function matchesQuery(label: string, query: string | undefined): boolean {
  return !query || label.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
}

function edgeMatches(edge: LandscapeEdge, filters: LandscapeFilters): boolean {
  if (filters.dependencyTypes?.size && !filters.dependencyTypes.has(edge.dependency_type)) return false;
  if (filters.mechanisms?.size && !filters.mechanisms.has(edge.mechanism)) return false;
  return true;
}

function businessStreamAsEdge(
  landscape: ProjectLandscapeResponse,
  stream: BusinessDataStream,
): TopologyEdge {
  const path = landscape.business_topology
    ? streamDeliveryPath(landscape.business_topology, stream)
    : undefined;
  const mechanisms = [...new Set(path?.segments.map((segment) => segment.mechanism).filter(Boolean) ?? [])];
  const mechanism = mechanisms.length === 1 ? mechanisms[0] : mechanisms.length > 1 ? "mixed" : "unknown";
  // Business claims use opaque evidence IDs. Resolve those IDs through the
  // API's identity-only catalogue before looking at scanner provenance; never
  // compare a claim ID with a display name.
  const claimedArtifacts = (landscape.topology_evidence?.artifacts ?? [])
    .filter((artifact) => stream.artifact_ids.includes(artifact.id));
  const artifactNames = new Set(claimedArtifacts.flatMap((artifact) => [artifact.name, artifact.job_id]));
  const rawProvenance = landscape.edges.flatMap((edge) => edge.provenance)
    .filter((record) => artifactNames.has(record.artifact_id) || artifactNames.has(record.qualified_artifact_id));
  // A validated business stream may cite an exact scanner artifact even when
  // the lower-level landscape edge did not classify that artifact as an
  // external dependency. Preserve that exact identity for L2 instead of
  // rendering an empty source/target canvas. This is not a name-based graph
  // inference: the stream's opaque artifact id already resolved through the
  // bounded topology-evidence catalogue above.
  const provenanceByIdentity = new Map<string, LandscapeProvenance>();
  for (const record of rawProvenance) {
    provenanceByIdentity.set(record.qualified_artifact_id, record);
  }
  for (const artifact of claimedArtifacts) {
    const artifactName = artifact.name || artifact.job_id;
    const qualifiedArtifactId = `${artifact.source_root}::${artifactName}`;
    if (!provenanceByIdentity.has(qualifiedArtifactId)) {
      provenanceByIdentity.set(qualifiedArtifactId, {
        artifact_id: artifactName,
        qualified_artifact_id: qualifiedArtifactId,
        source_root: artifact.source_root,
        component_name: "",
        fact_type: "business_topology_artifact",
      });
    }
  }
  // Preserve L2 when the curated overlay is present but its optional
  // evidence catalogue has not reached the API response yet. The declared
  // integration solution is still an exact business-topology implementation
  // reference; scanner artifacts remain preferred whenever available.
  if (provenanceByIdentity.size === 0 && stream.integration_solution_ids.length > 0) {
    for (const solutionId of stream.integration_solution_ids) {
      const solution = landscape.business_topology?.integration_solutions.find((item) => item.id === solutionId);
      if (!solution) continue;
      const qualifiedArtifactId = `business-solution::${solution.id}`;
      provenanceByIdentity.set(qualifiedArtifactId, {
        artifact_id: solution.name,
        qualified_artifact_id: qualifiedArtifactId,
        source_root: "business topology",
        component_name: "",
        fact_type: "business_topology_solution",
      });
    }
  }
  const provenance = [...provenanceByIdentity.values()];
  return {
    source: stream.source_system_id,
    target: stream.destination_system_id,
    source_kind: "business_system",
    target_kind: "business_system",
    dependency_type: "data",
    mechanism,
    direction: "out",
    reliability: path?.segments.some((segment) => /queue|jms|buffer/i.test(`${segment.mode} ${segment.mechanism}`)) ? "buffered" : "unknown",
    confidence: `${Math.round(stream.confidence * 100)}%`,
    evidence_count: stream.evidence_ids.length,
    provenance,
    businessStream: stream,
  };
}

function businessStreamMatches(
  landscape: ProjectLandscapeResponse,
  stream: BusinessDataStream,
  filters: LandscapeFilters,
): boolean {
  if (filters.dependencyTypes?.size && !filters.dependencyTypes.has("data")) return false;
  if (filters.mechanisms?.size) {
    const path = landscape.business_topology
      ? streamDeliveryPath(landscape.business_topology, stream)
      : undefined;
    if (!path?.segments.some((item) => filters.mechanisms!.has(item.mechanism))) return false;
  }
  return true;
}

const INFRASTRUCTURE_SYSTEM_PATTERN = /\b(log|logs|logdb|logging|monitor|monitoring|ldap|audit)\b/i;
const INFRASTRUCTURE_STREAM_PATTERN = /audit log|interface log|monitoring|authenticat|logging/i;

/**
 * Display-only heuristic (NOT a correctness gate) that flags observability
 * and auth "plumbing" streams -- logging, monitoring, LDAP authentication --
 * so the default business view can hide that clutter and read as a clean
 * hub-and-spoke diagram of the systems that actually move business data.
 * Nothing is removed from the underlying topology; this only affects what
 * is rendered by default.
 */
export function isInfrastructureStream(
  stream: BusinessDataStream,
  systemsById: Map<string, BusinessSystem>,
): boolean {
  const destination = systemsById.get(stream.destination_system_id);
  if (destination && INFRASTRUCTURE_SYSTEM_PATTERN.test(destination.name)) return true;
  return INFRASTRUCTURE_STREAM_PATTERN.test(stream.name) || INFRASTRUCTURE_STREAM_PATTERN.test(stream.business_summary);
}

function edgeColor(edge: LandscapeEdge): string {
  if (edge.reliability === "buffered" || edge.mechanism.toLocaleLowerCase().includes("jms")) return "#7c3aed";
  if (edge.dependency_type === "data") return "#0284c7";
  if (edge.dependency_type === "control") return "#475569";
  return "#64748b";
}

/** Purely project scanner entities and relationships into a bounded L1 graph. */
export function landscapeToFlow(
  landscape: ProjectLandscapeResponse,
  filters: LandscapeFilters = {},
): LandscapeFlow {
  if (landscape.business_topology && landscape.topology_state?.validated && !landscape.topology_state.stale) {
    return businessLandscapeToFlow(landscape, filters);
  }
  const candidateEdges = landscape.edges.filter((edge) => edgeMatches(edge, filters));
  const hasQuery = Boolean(filters.query?.trim());
  const matched = new Set([
    ...landscape.applications
      .filter((application) => matchesQuery(application.label || application.source_root, filters.query))
      .map((application) => application.id),
    ...landscape.systems
      .filter((system) => matchesQuery(system.name, filters.query))
      .map((system) => system.id),
  ]);
  // A search result keeps its direct scanner-computed neighbours for context,
  // but does not accidentally include the entire estate merely because every
  // entity participates in some relationship.
  const visibleFromQuery = hasQuery
    ? new Set([...matched, ...candidateEdges.filter((edge) => matched.has(edge.source) || matched.has(edge.target)).flatMap((edge) => [edge.source, edge.target])])
    : null;
  const participatingIds = new Set(
    candidateEdges.flatMap((edge) => [edge.source, edge.target]),
  );
  const hasRelationshipFilter = Boolean(
    filters.dependencyTypes?.size || filters.mechanisms?.size,
  );
  const limitToParticipants = candidateEdges.length > 0 || hasRelationshipFilter;
  const applications = landscape.applications.filter((application) =>
    visibleFromQuery
      ? visibleFromQuery.has(application.id)
      : !limitToParticipants || participatingIds.has(application.id),
  );
  const systems = filters.showSystems === false
    ? []
    : landscape.systems.filter((system) =>
      visibleFromQuery
        ? visibleFromQuery.has(system.id)
        : participatingIds.has(system.id),
    );
  const visibleIds = new Set([...applications.map((item) => item.id), ...systems.map((item) => item.id)]);

  const nodes: Node<LandscapeNodeData>[] = [
    ...applications.map((application) => ({
      id: application.id,
      type: "landscapeApplication",
      position: { x: 0, y: 0 },
      data: { entityType: "application" as const, application },
      ...APPLICATION_SIZE,
    })),
    ...systems.map((system) => ({
      id: system.id,
      type: "landscapeSystem",
      position: { x: 0, y: 0 },
      data: { entityType: "system" as const, system },
      ...SYSTEM_SIZE,
    })),
  ];

  const edges: Edge<{ landscapeEdge: LandscapeEdge }>[] = candidateEdges
    .filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target))
    .map((landscapeEdge) => ({
      id: landscapeEdgeKey(landscapeEdge),
      source: landscapeEdge.source,
      target: landscapeEdge.target,
      type: "smoothstep",
      animated: landscapeEdge.reliability === "buffered",
      label: `${topologyTerm(landscapeEdge.dependency_type)} · ${topologyTerm(landscapeEdge.mechanism)} · ${landscapeEdge.evidence_count}`,
      labelStyle: { fill: "#475569", fontSize: 10, fontWeight: 600 },
      labelBgStyle: { fill: "#ffffff", fillOpacity: 0.92 },
      labelBgPadding: [4, 2] as [number, number],
      markerEnd: { type: "arrowclosed" as const, color: edgeColor(landscapeEdge), width: 16, height: 16 },
      style: { stroke: edgeColor(landscapeEdge), strokeWidth: 1.8 },
      data: { landscapeEdge },
    }));

  const diagnostics = filters.diagnosticSeverities?.size
    ? landscape.diagnostics.filter((diagnostic) => filters.diagnosticSeverities!.has(diagnostic.severity))
    : landscape.diagnostics;
  return { nodes: layoutLandscape(nodes, edges), edges, diagnostics };
}

/** Project the agent-produced business contract into the organization L1
 * view. Scanner facts stay available for the technical fallback and L3/L4. */
function businessLandscapeToFlow(
  landscape: ProjectLandscapeResponse,
  filters: LandscapeFilters,
): LandscapeFlow {
  const business = landscape.business_topology!;
  const query = filters.query?.trim().toLocaleLowerCase() || "";
  const systemsById = new Map(business.systems.map((system) => [system.id, system]));
  const candidateStreams = business.data_streams
    .filter((stream) => businessStreamMatches(landscape, stream, filters))
    .filter((stream) => filters.showInfrastructure || !isInfrastructureStream(stream, systemsById));
  const queryMatches = new Set(
    business.systems
      .filter((system) =>
        !query
        || system.name.toLocaleLowerCase().includes(query)
        || system.aliases.some((alias) => alias.toLocaleLowerCase().includes(query)),
      )
      .map((system) => system.id),
  );
  const objectsById = new Map(business.business_objects.map((item) => [item.id, item]));
  const visibleStreams = query
    ? candidateStreams.filter((stream) =>
      queryMatches.has(stream.source_system_id)
      || queryMatches.has(stream.destination_system_id)
      || stream.name.toLocaleLowerCase().includes(query)
      || stream.business_object_ids.some((id) => objectsById.get(id)?.name.toLocaleLowerCase().includes(query)),
    )
    : candidateStreams;
  const connectedIds = new Set(
    visibleStreams.flatMap((stream) => [
      stream.source_system_id,
      stream.destination_system_id,
    ]),
  );
  const systems = business.systems.filter((system) => connectedIds.has(system.id));
  const nodes: Node<LandscapeNodeData>[] = systems.map((system) => ({
    id: system.id,
    type: "businessSystem",
    position: { x: 0, y: 0 },
    data: { entityType: "businessSystem" as const, system },
    ...SYSTEM_SIZE,
  }));
  const visibleIds = new Set(nodes.map((node) => node.id));
  const scopedStreams = visibleStreams.filter((stream) =>
    visibleIds.has(stream.source_system_id)
    && visibleIds.has(stream.destination_system_id)
    && systemsById.has(stream.source_system_id)
    && systemsById.has(stream.destination_system_id),
  );
  const edges: Edge<LandscapeEdgeData>[] = aggregateStreamsByPair(scopedStreams)
    .map(([pair, streams]) => aggregatedPairToEdge(landscape, objectsById, pair, streams));
  const diagnostics = filters.diagnosticSeverities?.size
    ? landscape.diagnostics.filter((diagnostic) =>
      filters.diagnosticSeverities!.has(diagnostic.severity),
    )
    : landscape.diagnostics;
  return { nodes: layoutBusinessLandscape(nodes, edges), edges, diagnostics };
}

interface DirectedPair {
  source: string;
  target: string;
}

/** Groups streams by directed system pair, preserving first-seen pair order
 * so rendering (and tests) stay deterministic across repeated calls. */
function aggregateStreamsByPair(
  streams: BusinessDataStream[],
): Array<[DirectedPair, BusinessDataStream[]]> {
  const order: string[] = [];
  const groups = new Map<string, { pair: DirectedPair; streams: BusinessDataStream[] }>();
  for (const stream of streams) {
    const key = `${stream.source_system_id}${stream.destination_system_id}`;
    let group = groups.get(key);
    if (!group) {
      group = { pair: { source: stream.source_system_id, target: stream.destination_system_id }, streams: [] };
      groups.set(key, group);
      order.push(key);
    }
    group.streams.push(stream);
  }
  return order.map((key) => {
    const group = groups.get(key)!;
    return [group.pair, group.streams] as [DirectedPair, BusinessDataStream[]];
  });
}

/** Up to this many distinct business-object names are listed on an
 * aggregated edge label before collapsing the remainder into "+N". */
const AGGREGATED_LABEL_OBJECT_LIMIT = 3;

function aggregatedPairLabel(
  streams: BusinessDataStream[],
  objectsById: Map<string, BusinessObject>,
): string {
  const objectNames = [...new Set(
    streams.flatMap((stream) => stream.business_object_ids.map((id) => objectsById.get(id)?.name)).filter(Boolean),
  )] as string[];
  if (objectNames.length === 0) return `${streams.length} flow${streams.length === 1 ? "" : "s"}`;
  const shown = objectNames.slice(0, AGGREGATED_LABEL_OBJECT_LIMIT).join(", ");
  const overflow = objectNames.length - AGGREGATED_LABEL_OBJECT_LIMIT;
  return overflow > 0 ? `${shown} +${overflow}` : shown;
}

/** Aggregate color/dash decisions read every constituent stream's own edge;
 * the visually "loudest" signal wins so a single buffered/unconfirmed stream
 * in a pair is never hidden behind the majority. */
function aggregatedPairColor(landscapeEdges: TopologyEdge[]): string {
  const buffered = landscapeEdges.find(
    (edge) => edge.reliability === "buffered" || edge.mechanism.toLocaleLowerCase().includes("jms"),
  );
  return edgeColor(buffered ?? landscapeEdges[0]);
}

function aggregatedPairToEdge(
  landscape: ProjectLandscapeResponse,
  objectsById: Map<string, BusinessObject>,
  pair: DirectedPair,
  streams: BusinessDataStream[],
): Edge<LandscapeEdgeData> {
  const landscapeEdges = streams.map((stream) => businessStreamAsEdge(landscape, stream));
  const allUnconfirmed = streams.every((stream) => stream.status !== "confirmed" && stream.status !== "observed");
  const color = aggregatedPairColor(landscapeEdges);
  return {
    id: aggregatedRelationshipId(pair.source, pair.target),
    source: pair.source,
    target: pair.target,
    type: "smoothstep",
    animated: landscapeEdges.some((edge) => edge.reliability === "buffered"),
    label: aggregatedPairLabel(streams, objectsById),
    labelStyle: { fill: "#334155", fontSize: 11, fontWeight: 650 },
    labelBgStyle: { fill: "#ffffff", fillOpacity: 0.96 },
    labelBgPadding: [6, 3] as [number, number],
    markerEnd: {
      type: "arrowclosed" as const,
      color,
      width: 17,
      height: 17,
    },
    style: {
      stroke: color,
      strokeWidth: Math.min(2 + streams.length * 0.4, 6),
      strokeDasharray: allUnconfirmed ? "7 4" : undefined,
    },
    data: {
      streamKeys: landscapeEdges.map((edge) => landscapeEdgeKey(edge)),
      pair,
    },
  };
}

export function layoutLandscape(
  nodes: Node<LandscapeNodeData>[],
  edges: Edge[],
): Node<LandscapeNodeData>[] {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "LR", nodesep: 42, ranksep: 112, marginx: 34, marginy: 34 });
  for (const node of nodes) graph.setNode(node.id, { width: node.width ?? 200, height: node.height ?? 100 });
  for (const edge of edges) graph.setEdge(edge.source, edge.target);
  dagre.layout(graph);
  return nodes.map((node) => {
    const point = graph.node(node.id);
    if (!point) return node;
    return {
      ...node,
      position: { x: point.x - (node.width ?? 200) / 2, y: point.y - (node.height ?? 100) / 2 },
    };
  });
}

/** Radial ring spacing: minimum gap between one ring and the next. */
const RING_GAP = 360;
/** Extra arc-length beyond a node's width reserved for each node on a ring, so
 * a busy ring simply grows its radius instead of overlapping its own nodes. */
const NODE_ARC_GAP = 120;
/** Angular stagger applied per ring so successive rings don't align radially
 * and stack their edges on top of each other. */
const RING_ANGLE_OFFSET = 0.35;
/** Horizontal gutter between tiled connected components. */
const COMPONENT_GUTTER = 200;

function buildUndirectedAdjacency(nodeIds: string[], edges: Edge[]): Map<string, Set<string>> {
  const adjacency = new Map<string, Set<string>>(nodeIds.map((id) => [id, new Set<string>()]));
  for (const edge of edges) {
    if (!adjacency.has(edge.source) || !adjacency.has(edge.target)) continue;
    adjacency.get(edge.source)!.add(edge.target);
    adjacency.get(edge.target)!.add(edge.source);
  }
  return adjacency;
}

function findConnectedComponents(nodeIds: string[], adjacency: Map<string, Set<string>>): string[][] {
  const seen = new Set<string>();
  const components: string[][] = [];
  for (const id of nodeIds) {
    if (seen.has(id)) continue;
    const component: string[] = [];
    const queue = [id];
    seen.add(id);
    while (queue.length > 0) {
      const current = queue.shift()!;
      component.push(current);
      for (const neighbor of adjacency.get(current) ?? []) {
        if (!seen.has(neighbor)) {
          seen.add(neighbor);
          queue.push(neighbor);
        }
      }
    }
    components.push(component);
  }
  return components;
}

/** Highest undirected-degree node in the component; ties broken by ascending
 * node id so the chosen hub is stable across runs. */
function pickComponentHub(componentIds: string[], adjacency: Map<string, Set<string>>): string {
  return [...componentIds].sort((a, b) => {
    const degreeDiff = (adjacency.get(b)?.size ?? 0) - (adjacency.get(a)?.size ?? 0);
    return degreeDiff !== 0 ? degreeDiff : a.localeCompare(b);
  })[0];
}

/** BFS from the hub assigning a ring index (hub = 0, its neighbours = 1, …). */
function assignRings(hub: string, adjacency: Map<string, Set<string>>): Map<string, number> {
  const rings = new Map<string, number>([[hub, 0]]);
  const queue = [hub];
  while (queue.length > 0) {
    const current = queue.shift()!;
    const currentRing = rings.get(current)!;
    for (const neighbor of [...(adjacency.get(current) ?? [])].sort()) {
      if (!rings.has(neighbor)) {
        rings.set(neighbor, currentRing + 1);
        queue.push(neighbor);
      }
    }
  }
  return rings;
}

interface RadialPlacement {
  id: string;
  centerX: number;
  centerY: number;
  width: number;
  height: number;
}

function placeComponentRadially(
  componentIds: string[],
  hub: string,
  rings: Map<string, number>,
  sizeById: Map<string, { width: number; height: number }>,
): RadialPlacement[] {
  const byRing = new Map<number, string[]>();
  for (const id of componentIds) {
    const ring = rings.get(id) ?? 0;
    if (!byRing.has(ring)) byRing.set(ring, []);
    byRing.get(ring)!.push(id);
  }
  const placements: RadialPlacement[] = [];
  const size = (id: string) => sizeById.get(id) ?? SYSTEM_SIZE;
  // Minimum arc-length each node needs so neighbours on the same ring never
  // overlap (node width + breathing room).
  const perNodeArc = SYSTEM_SIZE.width + NODE_ARC_GAP;
  // Rings must be processed in order so each sits fully outside the previous
  // one -- otherwise a crowded inner ring (a hub with many direct neighbours)
  // grows past the next ring's fixed radius and the two collide.
  let prevRadius = 0;
  for (const ring of [...byRing.keys()].sort((a, b) => a - b)) {
    const idsInRing = byRing.get(ring)!;
    if (ring === 0) {
      const { width, height } = size(hub);
      placements.push({ id: hub, centerX: 0, centerY: 0, width, height });
      prevRadius = Math.max(width, height) / 2;
      continue;
    }
    const orderedIds = [...idsInRing].sort();
    const count = orderedIds.length;
    // Radius wide enough to (a) clear the previous ring and (b) give every node
    // on THIS ring its arc-length, so nothing overlaps however busy the ring is.
    const circumferenceRadius = count > 1 ? (count * perNodeArc) / (2 * Math.PI) : 0;
    const radius = Math.max(prevRadius + RING_GAP, circumferenceRadius);
    const angleOffset = ring * RING_ANGLE_OFFSET;
    orderedIds.forEach((id, index) => {
      const angle = count === 1 ? angleOffset : (index / count) * 2 * Math.PI + angleOffset;
      const { width, height } = size(id);
      placements.push({
        id,
        centerX: radius * Math.cos(angle),
        centerY: radius * Math.sin(angle),
        width,
        height,
      });
    });
    prevRadius = radius;
  }
  return placements;
}

function radialBoundingBox(placements: RadialPlacement[]): { minX: number; minY: number; maxX: number; maxY: number } {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const placement of placements) {
    minX = Math.min(minX, placement.centerX - placement.width / 2);
    maxX = Math.max(maxX, placement.centerX + placement.width / 2);
    minY = Math.min(minY, placement.centerY - placement.height / 2);
    maxY = Math.max(maxY, placement.centerY + placement.height / 2);
  }
  return { minX, minY, maxX, maxY };
}

/**
 * Hub-and-spoke radial layout for the business landscape view. Unlike
 * `layoutLandscape` (a directed LR layered dagre layout, kept for the
 * technical view), this treats edges as undirected connectivity: each
 * connected component finds its highest-degree node as a local hub, places
 * it at the component's origin, then rings its neighbours outward by BFS
 * distance on concentric circles (`RING_GAP` apart, evenly distributed by
 * angle, with a per-ring angular stagger so rings don't visually align).
 * Multiple components are tiled left-to-right, largest first, so disjoint
 * clusters (e.g. a standalone auth/logging pair) never overlap the main hub.
 */
export function layoutBusinessLandscape(
  nodes: Node<LandscapeNodeData>[],
  edges: Edge[],
): Node<LandscapeNodeData>[] {
  const nodeIds = nodes.map((node) => node.id);
  const sizeById = new Map(nodes.map((node) => [
    node.id,
    { width: node.width ?? SYSTEM_SIZE.width, height: node.height ?? SYSTEM_SIZE.height },
  ]));
  const adjacency = buildUndirectedAdjacency(nodeIds, edges);
  const components = findConnectedComponents(nodeIds, adjacency)
    .map((componentIds) => [...componentIds].sort())
    .sort((a, b) => b.length - a.length || a[0].localeCompare(b[0]));

  const centers = new Map<string, { x: number; y: number }>();
  let cursorX = 0;
  for (const componentIds of components) {
    const hub = pickComponentHub(componentIds, adjacency);
    const rings = assignRings(hub, adjacency);
    const placements = placeComponentRadially(componentIds, hub, rings, sizeById);
    const box = radialBoundingBox(placements);
    const shiftX = cursorX - box.minX;
    const shiftY = -((box.minY + box.maxY) / 2);
    for (const placement of placements) {
      centers.set(placement.id, { x: placement.centerX + shiftX, y: placement.centerY + shiftY });
    }
    cursorX += (box.maxX - box.minX) + COMPONENT_GUTTER;
  }

  return nodes.map((node) => {
    const center = centers.get(node.id);
    if (!center) return node;
    const { width, height } = sizeById.get(node.id) ?? SYSTEM_SIZE;
    return {
      ...node,
      position: { x: center.x - width / 2, y: center.y - height / 2 },
    };
  });
}

export function findLandscapeEdge(
  landscape: ProjectLandscapeResponse,
  key: string,
): TopologyEdge | undefined {
  const business = landscape.business_topology?.data_streams.find(
    (stream) => `business-stream\u001f${stream.id}` === key,
  );
  if (business) return businessStreamAsEdge(landscape, business);
  return landscape.edges.find((edge) => landscapeEdgeKey(edge) === key);
}

export function connectedEdges(
  landscape: ProjectLandscapeResponse,
  entityId: string,
): TopologyEdge[] {
  if (landscape.business_topology && landscape.topology_state?.validated && !landscape.topology_state.stale) {
    return landscape.business_topology.data_streams
      .filter((stream) =>
        stream.source_system_id === entityId || stream.destination_system_id === entityId,
      )
      .map((stream) => businessStreamAsEdge(landscape, stream));
  }
  return landscape.edges.filter((edge) => edge.source === entityId || edge.target === entityId);
}

/** Resolves the constituent stream keys carried by an aggregated L1
 * system-pair edge (`AggregatedRelationshipEdgeData.streamKeys`) back to
 * their individual `TopologyEdge`s, in stream order, dropping any key that
 * no longer resolves (e.g. a stale selection after the topology changed). */
export function resolveRelationshipEdges(
  landscape: ProjectLandscapeResponse,
  streamKeys: string[],
): TopologyEdge[] {
  return streamKeys
    .map((key) => findLandscapeEdge(landscape, key))
    .filter((edge): edge is TopologyEdge => Boolean(edge));
}
