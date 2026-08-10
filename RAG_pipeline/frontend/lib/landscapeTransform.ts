import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";
import type {
  LandscapeApplication,
  LandscapeDiagnostic,
  LandscapeEdge,
  LandscapeSystem,
  ProjectLandscapeResponse,
  BusinessDataStream,
  BusinessSystem,
} from "@/types/landscape";

export type LandscapeNodeData =
  | { entityType: "application"; application: LandscapeApplication }
  | { entityType: "system"; system: LandscapeSystem }
  | { entityType: "businessSystem"; system: BusinessSystem };

export type TopologyEdge = LandscapeEdge & {
  businessStream?: BusinessDataStream;
};

export interface LandscapeFilters {
  query?: string;
  dependencyTypes?: Set<string>;
  mechanisms?: Set<string>;
  showSystems?: boolean;
  diagnosticSeverities?: Set<string>;
}

export interface LandscapeFlow {
  nodes: Node<LandscapeNodeData>[];
  edges: Edge<{ landscapeEdge: TopologyEdge }>[];
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
  const path = stream.delivery_path_id
    ? landscape.business_topology?.delivery_paths.find((item) => item.id === stream.delivery_path_id)
    : undefined;
  const mechanisms = [...new Set(path?.segments.map((segment) => segment.mechanism).filter(Boolean) ?? [])];
  const mechanism = mechanisms.length === 1 ? mechanisms[0] : mechanisms.length > 1 ? "mixed" : "unknown";
  // Business claims use opaque evidence IDs. Resolve those IDs through the
  // API's identity-only catalogue before looking at scanner provenance; never
  // compare a claim ID with a display name.
  const claimedArtifacts = (landscape.topology_evidence?.artifacts ?? [])
    .filter((artifact) => stream.artifact_ids.includes(artifact.id));
  const artifactNames = new Set(claimedArtifacts.flatMap((artifact) => [artifact.name, artifact.job_id]));
  const provenance = landscape.edges.flatMap((edge) => edge.provenance)
    .filter((record) => artifactNames.has(record.artifact_id) || artifactNames.has(record.qualified_artifact_id));
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
    const path = stream.delivery_path_id
      ? landscape.business_topology?.delivery_paths.find((item) => item.id === stream.delivery_path_id)
      : undefined;
    if (!path?.segments.some((item) => filters.mechanisms!.has(item.mechanism))) return false;
  }
  return true;
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
  const candidateStreams = business.data_streams.filter((stream) =>
    businessStreamMatches(landscape, stream, filters),
  );
  const systemsById = new Map(business.systems.map((system) => [system.id, system]));
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
  const edges: Edge<{ landscapeEdge: TopologyEdge }>[] = visibleStreams
    .filter((stream) =>
      visibleIds.has(stream.source_system_id)
      && visibleIds.has(stream.destination_system_id)
      && systemsById.has(stream.source_system_id)
      && systemsById.has(stream.destination_system_id),
    )
    .map((stream) => {
      const landscapeEdge = businessStreamAsEdge(landscape, stream);
      const objectNames = stream.business_object_ids
        .map((id) => objectsById.get(id)?.name)
        .filter(Boolean);
      return {
        id: landscapeEdgeKey(landscapeEdge),
        source: stream.source_system_id,
        target: stream.destination_system_id,
        type: "smoothstep",
        animated: landscapeEdge.reliability === "buffered",
        label: objectNames.join(", ") || stream.name,
        labelStyle: { fill: "#334155", fontSize: 11, fontWeight: 650 },
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.96 },
        labelBgPadding: [6, 3] as [number, number],
        markerEnd: {
          type: "arrowclosed" as const,
          color: edgeColor(landscapeEdge),
          width: 17,
          height: 17,
        },
        style: {
          stroke: edgeColor(landscapeEdge),
          strokeWidth: 2,
          strokeDasharray: stream.status === "confirmed" || stream.status === "observed" ? undefined : "7 4",
        },
        data: { landscapeEdge },
      };
    });
  const diagnostics = filters.diagnosticSeverities?.size
    ? landscape.diagnostics.filter((diagnostic) =>
      filters.diagnosticSeverities!.has(diagnostic.severity),
    )
    : landscape.diagnostics;
  return { nodes: layoutLandscape(nodes, edges), edges, diagnostics };
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
