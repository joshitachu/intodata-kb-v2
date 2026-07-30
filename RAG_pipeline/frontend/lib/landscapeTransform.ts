import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";
import type {
  LandscapeApplication,
  LandscapeDiagnostic,
  LandscapeEdge,
  LandscapeSystem,
  ProjectLandscapeResponse,
} from "@/types/landscape";

export type LandscapeNodeData =
  | { entityType: "application"; application: LandscapeApplication }
  | { entityType: "system"; system: LandscapeSystem };

export interface LandscapeFilters {
  query?: string;
  dependencyTypes?: Set<string>;
  mechanisms?: Set<string>;
  showSystems?: boolean;
  diagnosticSeverities?: Set<string>;
}

export interface LandscapeFlow {
  nodes: Node<LandscapeNodeData>[];
  edges: Edge<{ landscapeEdge: LandscapeEdge }>[];
  diagnostics: LandscapeDiagnostic[];
}

/** Stable identity for an aggregated fact; labels and array position are never identity. */
export function landscapeEdgeKey(edge: LandscapeEdge): string {
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
  return system?.name || entityId;
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
  const applications = landscape.applications.filter((application) =>
    !visibleFromQuery || visibleFromQuery.has(application.id),
  );
  const participatingIds = new Set(
    candidateEdges.flatMap((edge) => [edge.source, edge.target]),
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
): LandscapeEdge | undefined {
  return landscape.edges.find((edge) => landscapeEdgeKey(edge) === key);
}

export function connectedEdges(
  landscape: ProjectLandscapeResponse,
  entityId: string,
): LandscapeEdge[] {
  return landscape.edges.filter((edge) => edge.source === entityId || edge.target === entityId);
}
