import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";
import type {
  LandscapeApplication,
  LandscapeDiagnostic,
  LandscapeEdge,
  LandscapeSystem,
  ProjectLandscapeResponse,
  SemanticExchange,
  SemanticSystem,
} from "@/types/landscape";

export type LandscapeNodeData =
  | { entityType: "application"; application: LandscapeApplication }
  | { entityType: "system"; system: LandscapeSystem }
  | { entityType: "semanticSystem"; system: SemanticSystem };

export type TopologyEdge = LandscapeEdge & {
  semanticExchange?: SemanticExchange;
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
  const semanticId = (edge as TopologyEdge).semanticExchange?.id;
  if (semanticId) return `semantic-exchange\u001f${semanticId}`;
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
  return landscape.semantic_topology?.systems.find((item) => item.id === entityId)?.name || entityId;
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

function semanticExchangeAsEdge(exchange: SemanticExchange): TopologyEdge {
  const mechanism = exchange.mechanisms.length === 1
    ? exchange.mechanisms[0]
    : exchange.mechanisms.length > 1
      ? "mixed"
      : "unknown";
  return {
    source: exchange.source_system_id,
    target: exchange.target_system_id,
    source_kind: "semantic_system",
    target_kind: "semantic_system",
    dependency_type: "data",
    mechanism,
    direction: "out",
    reliability: exchange.reliability,
    confidence: exchange.confidence,
    evidence_count: exchange.evidence.length,
    provenance: exchange.evidence
      .filter((record) => record.artifact_id && record.qualified_artifact_id)
      .map((record) => ({
        artifact_id: record.artifact_id,
        qualified_artifact_id: record.qualified_artifact_id,
        source_root: record.source_root,
        component_name: record.component_name,
        fact_type: record.signal_type,
      })),
    semanticExchange: exchange,
  };
}

function semanticExchangeMatches(
  exchange: SemanticExchange,
  filters: LandscapeFilters,
): boolean {
  if (filters.dependencyTypes?.size && !filters.dependencyTypes.has("data")) return false;
  if (
    filters.mechanisms?.size
    && !exchange.mechanisms.some((item) => filters.mechanisms!.has(item))
  ) return false;
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
  if (landscape.semantic_topology?.exchanges.length) {
    return semanticLandscapeToFlow(landscape, filters);
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

/** Project the evidence-backed semantic layer into the organization L1 view.
 *
 * Talend projects remain available in `integration_solutions` for L2/L3, but
 * are intentionally not drawn as organization systems. L1 shows only the
 * proposed/confirmed business systems and meaningful exchanges between them.
 */
function semanticLandscapeToFlow(
  landscape: ProjectLandscapeResponse,
  filters: LandscapeFilters,
): LandscapeFlow {
  const semantic = landscape.semantic_topology!;
  const query = filters.query?.trim().toLocaleLowerCase() || "";
  const candidateExchanges = semantic.exchanges.filter((exchange) =>
    semanticExchangeMatches(exchange, filters),
  );
  const systemsById = new Map(semantic.systems.map((system) => [system.id, system]));
  const queryMatches = new Set(
    semantic.systems
      .filter((system) =>
        !query
        || system.name.toLocaleLowerCase().includes(query)
        || system.aliases.some((alias) => alias.toLocaleLowerCase().includes(query)),
      )
      .map((system) => system.id),
  );
  const visibleExchanges = query
    ? candidateExchanges.filter((exchange) =>
      queryMatches.has(exchange.source_system_id)
      || queryMatches.has(exchange.target_system_id)
      || exchange.label.toLocaleLowerCase().includes(query)
      || exchange.payloads.some((payload) => payload.toLocaleLowerCase().includes(query)),
    )
    : candidateExchanges;
  const connectedIds = new Set(
    visibleExchanges.flatMap((exchange) => [
      exchange.source_system_id,
      exchange.target_system_id,
    ]),
  );
  const systems = semantic.systems.filter((system) => connectedIds.has(system.id));
  const nodes: Node<LandscapeNodeData>[] = systems.map((system) => ({
    id: system.id,
    type: "semanticSystem",
    position: { x: 0, y: 0 },
    data: { entityType: "semanticSystem" as const, system },
    ...SYSTEM_SIZE,
  }));
  const visibleIds = new Set(nodes.map((node) => node.id));
  const edges: Edge<{ landscapeEdge: TopologyEdge }>[] = visibleExchanges
    .filter((exchange) =>
      visibleIds.has(exchange.source_system_id)
      && visibleIds.has(exchange.target_system_id)
      && systemsById.has(exchange.source_system_id)
      && systemsById.has(exchange.target_system_id),
    )
    .map((exchange) => {
      const landscapeEdge = semanticExchangeAsEdge(exchange);
      return {
        id: landscapeEdgeKey(landscapeEdge),
        source: exchange.source_system_id,
        target: exchange.target_system_id,
        type: "smoothstep",
        animated: exchange.reliability === "buffered",
        label: exchange.label,
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
          strokeDasharray: exchange.status === "confirmed" ? undefined : "7 4",
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
  const semantic = landscape.semantic_topology?.exchanges.find(
    (exchange) => `semantic-exchange\u001f${exchange.id}` === key,
  );
  if (semantic) return semanticExchangeAsEdge(semantic);
  return landscape.edges.find((edge) => landscapeEdgeKey(edge) === key);
}

export function connectedEdges(
  landscape: ProjectLandscapeResponse,
  entityId: string,
): TopologyEdge[] {
  if (landscape.semantic_topology?.exchanges.length) {
    return landscape.semantic_topology.exchanges
      .filter((edge) =>
        edge.source_system_id === entityId || edge.target_system_id === entityId,
      )
      .map(semanticExchangeAsEdge);
  }
  return landscape.edges.filter((edge) => edge.source === entityId || edge.target === entityId);
}
