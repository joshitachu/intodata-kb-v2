import type {
  BusinessDataStream,
  BusinessIntegrationSolution,
  BusinessObject,
  BusinessSystem,
  BusinessTopology,
  BusinessDeliveryPath,
  BusinessTransformation,
} from "@/types/landscape";

/**
 * A deliberately small, renderer-independent L1 projection of the evidence
 * topology. It is intentionally separate from React Flow: this is the
 * architecture contract a canvas, exported diagram, or API can consume.
 */
export type ArchitectureLayer = "external" | "integration" | "core" | "supporting";
export type ArchitectureColumn = "upstream" | "anchor" | "downstream" | "secondary" | "supporting" | "integration";
export type ArchitectureNodeKind = "system" | "integration" | "broker" | "system_group";
export type ArchitectureRelationshipStatus = "confirmed" | "inferred" | "proposed" | "mixed";

export interface ArchitectureNode {
  id: string;
  label: string;
  description: string;
  layer: ArchitectureLayer;
  column: ArchitectureColumn;
  kind: ArchitectureNodeKind;
  /** Raw business-system ids represented by this node (one or many for a group). */
  systemIds: string[];
  /** Raw integration-solution ids represented by this node. */
  integrationSolutionIds: string[];
  degree: number;
  status: ArchitectureRelationshipStatus;
}

export interface ArchitectureRelationship {
  id: string;
  source: string;
  target: string;
  /** Human-readable, collapsed business objects; detailed streams stay below. */
  businessObjects: string[];
  integrationSolutionIds: string[];
  protocols: string[];
  status: ArchitectureRelationshipStatus;
  /** The exact raw stream ids that produced this one architectural edge. */
  streamIds: string[];
}

export interface ArchitectureL1ViewModel {
  level: "L1";
  title: string;
  anchorId: string;
  source: "business_topology";
  nodes: ArchitectureNode[];
  relationships: ArchitectureRelationship[];
  /** Relationships deliberately omitted from the quiet default map. They are still available in L2. */
  hiddenRelationshipIds: string[];
  /** Streams that only connect systems inside one collapsed group. */
  hiddenInternalStreamIds: string[];
}

export interface ArchitecturePairRelationship {
  id: string;
  first: string;
  second: string;
  directions: ArchitectureRelationship[];
  businessObjects: string[];
  integrationSolutionIds: string[];
  protocols: string[];
  status: ArchitectureRelationshipStatus;
  streamIds: string[];
}

export interface ArchitectureFlowProjection {
  id: string;
  sourceSystemId: string;
  destinationSystemId: string;
  name: string;
  summary: string;
  businessObjects: string[];
  integrationSolutions: string[];
  protocols: string[];
  status: ArchitectureRelationshipStatus;
  artifactIds: string[];
  evidenceIds: string[];
  deliveryPath?: BusinessDeliveryPath;
  transformations: BusinessTransformation[];
}

const CORE_SYSTEM_IDS = new Set([
  "sys-maximo",
  "sys-4ps",
  "sys-quintiq",
  "sys-rhm",
  "sys-vise",
]);

const INTEGRATION_SYSTEM_IDS = new Set([
  "sys-enterpriseservicebus",
  "sys-activemq",
]);

const SUPPORTING_SYSTEM_IDS = new Set([
  "sys-centric",
  "sys-relatics",
  "sys-sharepoint",
  "sys-irissys",
  "sys-prorail",
  "sys-dds-visp-unresolved",
  "sys-external-unspecified",
]);

const SUPPORTING_GROUP_ID = "group:supporting-and-unresolved";
const SPECIALISED_TALEND_ID = "integration:specialised-talend";
const MAX_VISIBLE_RELATIONSHIPS = 18;
const ANCHOR_NODE_ID = "system:sys-maximo";

const WERKORDER_COLUMNS: Record<string, ArchitectureColumn> = {
  "sys-afas": "upstream",
  "sys-ortec": "upstream",
  "sys-forsuppliers": "upstream",
  "sys-maximo": "anchor",
  "sys-4ps": "downstream",
  "sys-quintiq": "downstream",
  "sys-assetcheck": "downstream",
  "sys-rhm": "downstream",
  "sys-vise": "secondary",
  "sys-smartflow": "secondary",
};

function statusRank(status: string): number {
  switch (status) {
    case "confirmed":
    case "observed":
      return 3;
    case "inferred":
      return 2;
    case "proposed":
      return 1;
    default:
      return 0;
  }
}

function combinedStatus(statuses: ArchitectureRelationshipStatus[]): ArchitectureRelationshipStatus {
  const uniqueStatuses = unique(statuses);
  return uniqueStatuses.length === 1 ? uniqueStatuses[0] : "mixed";
}

function relationshipStatus(statuses: string[]): ArchitectureRelationshipStatus {
  const unique = new Set(statuses);
  if (unique.size > 1) return "mixed";
  const only = statuses[0];
  if (only === "confirmed" || only === "observed") return "confirmed";
  if (only === "inferred") return "inferred";
  return "proposed";
}

function streamProtocols(stream: BusinessDataStream): string[] {
  if (stream.protocols.length > 0) return stream.protocols;
  const text = `${stream.name} ${stream.business_summary} ${stream.description}`.toLocaleLowerCase();
  const protocols: string[] = [];
  if (/\bjms\b|activemq|message queue/.test(text)) protocols.push("JMS / ActiveMQ");
  if (/\brest\b|https?:\/\//.test(text)) protocols.push("REST");
  if (/\bsoap\b/.test(text)) protocols.push("SOAP");
  if (/\bfile\b|\bxml\b/.test(text)) protocols.push("File");
  if (/\bjdbc\b|\bdatabase\b|\bmssql\b|\boracle\b|\bsql\b/.test(text)) protocols.push("Database");
  return protocols.length ? protocols : ["Not established"];
}

function titleFromSystem(system: BusinessSystem): string {
  return system.description || system.purpose || "Business system";
}

function systemNodeId(systemId: string): string {
  return SUPPORTING_SYSTEM_IDS.has(systemId) ? SUPPORTING_GROUP_ID : `system:${systemId}`;
}

function columnForSystem(systemId: string, layer: ArchitectureLayer): ArchitectureColumn {
  return WERKORDER_COLUMNS[systemId] ?? (layer === "supporting" ? "supporting" : layer === "integration" ? "integration" : "secondary");
}

function isSpecialisedTalend(solution: BusinessIntegrationSolution): boolean {
  return solution.id !== "int-sol-ettm" && solution.id !== "int-sol-enterpriseservicebus";
}

function architectureNodeForIntegration(solution: BusinessIntegrationSolution): ArchitectureNode {
  if (solution.id === "int-sol-ettm") {
    return {
      id: "integration:ettm", label: "ETTM", description: "Talend integration project", layer: "integration", column: "integration", kind: "integration",
      systemIds: [], integrationSolutionIds: [solution.id], degree: 0, status: "confirmed",
    };
  }
  if (solution.id === "int-sol-enterpriseservicebus") {
    return {
      id: "integration:enterpriseservicebus", label: "EnterpriseServiceBus", description: "Talend message relay and routing", layer: "integration", column: "integration", kind: "integration",
      systemIds: ["sys-enterpriseservicebus"], integrationSolutionIds: [solution.id], degree: 0, status: "confirmed",
    };
  }
  return {
    id: SPECIALISED_TALEND_ID,
    label: "Specialised Talend integrations",
    description: "AssetCheck and VQ project integrations; expand in L2 for individual solutions.",
    layer: "integration",
    column: "integration",
    kind: "integration",
    systemIds: [],
    integrationSolutionIds: [solution.id],
    degree: 0,
    status: "confirmed",
  };
}

function integrationNodeId(solutionId: string): string {
  if (solutionId === "int-sol-ettm") return "integration:ettm";
  if (solutionId === "int-sol-enterpriseservicebus") return "integration:enterpriseservicebus";
  return SPECIALISED_TALEND_ID;
}

function relationshipId(source: string, target: string): string {
  return `relationship:${source}->${target}`;
}

function unique<T>(items: T[]): T[] {
  return [...new Set(items)];
}

/**
 * Produces the quiet company architecture map from raw evidence. Systems are
 * curated into layers, lower-value systems are grouped, and any number of
 * streams between the same visible systems become one relationship. Nothing
 * is discarded: every relationship keeps its raw stream ids for L2/L3.
 */
export function normalizeArchitectureL1(topology: BusinessTopology): ArchitectureL1ViewModel {
  const systemById = new Map(topology.systems.map((system) => [system.id, system]));
  const objectById = new Map(topology.business_objects.map((object) => [object.id, object]));

  const nodes = new Map<string, ArchitectureNode>();
  for (const system of topology.systems) {
    if (INTEGRATION_SYSTEM_IDS.has(system.id)) continue;
    const id = systemNodeId(system.id);
    if (id === SUPPORTING_GROUP_ID) continue;
    const layer: ArchitectureLayer = CORE_SYSTEM_IDS.has(system.id) ? "core" : "external";
    nodes.set(id, {
      id,
      label: system.name,
      description: titleFromSystem(system),
      layer,
      column: columnForSystem(system.id, layer),
      kind: "system",
      systemIds: [system.id],
      integrationSolutionIds: [],
      degree: 0,
      status: relationshipStatus([system.status]),
    });
  }

  const groupedSystems = topology.systems.filter((system) => SUPPORTING_SYSTEM_IDS.has(system.id));
  if (groupedSystems.length) {
    nodes.set(SUPPORTING_GROUP_ID, {
      id: SUPPORTING_GROUP_ID,
      label: "Other supporting & unresolved systems",
      description: groupedSystems.map((system) => system.name).join(" · "),
      layer: "supporting",
      column: "supporting",
      kind: "system_group",
      systemIds: groupedSystems.map((system) => system.id),
      integrationSolutionIds: [],
      degree: 0,
      status: relationshipStatus(groupedSystems.map((system) => system.status)),
    });
  }

  for (const solution of topology.integration_solutions) {
    const node = architectureNodeForIntegration(solution);
    const existing = nodes.get(node.id);
    if (existing) {
      existing.integrationSolutionIds.push(solution.id);
    } else {
      nodes.set(node.id, node);
    }
  }
  const activeMq = systemById.get("sys-activemq");
  if (activeMq) {
    nodes.set("integration:activemq", {
      id: "integration:activemq", label: "ActiveMQ", description: titleFromSystem(activeMq), layer: "integration", column: "integration", kind: "broker",
      systemIds: [activeMq.id], integrationSolutionIds: [], degree: 0,
      status: relationshipStatus([activeMq.status]),
    });
  }

  const grouped = new Map<string, BusinessDataStream[]>();
  const hiddenInternalStreamIds: string[] = [];
  for (const stream of topology.data_streams) {
    if (!systemById.has(stream.source_system_id) || !systemById.has(stream.destination_system_id)) continue;
    const source = systemNodeId(stream.source_system_id);
    const target = systemNodeId(stream.destination_system_id);
    if (source === target) {
      hiddenInternalStreamIds.push(stream.id);
      continue;
    }
    const id = relationshipId(source, target);
    const streams = grouped.get(id) ?? [];
    streams.push(stream);
    grouped.set(id, streams);
  }

  const relationships = [...grouped.entries()].map(([id, streams]) => {
    const first = streams[0];
    return {
      id,
      source: systemNodeId(first.source_system_id),
      target: systemNodeId(first.destination_system_id),
      businessObjects: unique(streams.flatMap((stream) => stream.business_object_ids.map((objectId) => objectById.get(objectId)?.name).filter(Boolean) as string[])),
      integrationSolutionIds: unique(streams.flatMap((stream) => stream.integration_solution_ids)),
      protocols: unique(streams.flatMap(streamProtocols)),
      status: relationshipStatus(streams.map((stream) => stream.status)),
      streamIds: streams.map((stream) => stream.id),
    } satisfies ArchitectureRelationship;
  });

  // Prefer strongly evidenced, high-volume relationships for the quiet default
  // map. L2 retains every omitted relationship and every individual stream.
  const sorted = [...relationships].sort((a, b) => {
    const aScore = Math.max(...a.streamIds.map((id) => statusRank(topology.data_streams.find((stream) => stream.id === id)?.status ?? ""))) * 100 + a.streamIds.length;
    const bScore = Math.max(...b.streamIds.map((id) => statusRank(topology.data_streams.find((stream) => stream.id === id)?.status ?? ""))) * 100 + b.streamIds.length;
    return bScore - aScore || a.id.localeCompare(b.id);
  });
  const visibleRelationships = sorted.slice(0, MAX_VISIBLE_RELATIONSHIPS);
  const visibleNodeIds = new Set(visibleRelationships.flatMap((relationship) => [relationship.source, relationship.target]));
  const nodeDegree = new Map<string, number>();
  for (const relationship of visibleRelationships) {
    nodeDegree.set(relationship.source, (nodeDegree.get(relationship.source) ?? 0) + 1);
    nodeDegree.set(relationship.target, (nodeDegree.get(relationship.target) ?? 0) + 1);
  }
  for (const node of nodes.values()) node.degree = nodeDegree.get(node.id) ?? 0;

  // Integration nodes are intentionally retained even though their use is
  // carried as metadata on a collapsed system-to-system relationship. This
  // supports a later "show integration routing" mode without changing data.
  const visibleNodes = [...nodes.values()]
    .filter((node) => visibleNodeIds.has(node.id) || node.layer === "integration")
    .sort((a, b) => a.layer.localeCompare(b.layer) || b.degree - a.degree || a.label.localeCompare(b.label));

  return {
    level: "L1",
    title: "Company architecture overview",
    anchorId: ANCHOR_NODE_ID,
    source: "business_topology",
    nodes: visibleNodes,
    relationships: visibleRelationships,
    hiddenRelationshipIds: sorted.slice(MAX_VISIBLE_RELATIONSHIPS).map((relationship) => relationship.id),
    hiddenInternalStreamIds,
  };
}

/** One visible L1 connection per unordered system pair. Opposite directional
 * streams are retained in `directions` and become visible only after focus. */
export function architectureRelationshipPairs(
  model: ArchitectureL1ViewModel,
): ArchitecturePairRelationship[] {
  const groups = new Map<string, ArchitectureRelationship[]>();
  for (const relationship of model.relationships) {
    const endpoints = [relationship.source, relationship.target].sort();
    const id = `pair:${endpoints[0]}<->${endpoints[1]}`;
    groups.set(id, [...(groups.get(id) ?? []), relationship]);
  }
  return [...groups.entries()].map(([id, directions]) => ({
    id,
    first: directions[0].source < directions[0].target ? directions[0].source : directions[0].target,
    second: directions[0].source < directions[0].target ? directions[0].target : directions[0].source,
    directions,
    businessObjects: unique(directions.flatMap((direction) => direction.businessObjects)),
    integrationSolutionIds: unique(directions.flatMap((direction) => direction.integrationSolutionIds)),
    protocols: unique(directions.flatMap((direction) => direction.protocols)),
    status: combinedStatus(directions.map((direction) => direction.status)),
    streamIds: directions.flatMap((direction) => direction.streamIds),
  }));
}

export function architectureFlowsForPair(
  topology: BusinessTopology,
  pair: ArchitecturePairRelationship,
): ArchitectureFlowProjection[] {
  const objectById = new Map(topology.business_objects.map((object) => [object.id, object.name]));
  const solutionById = new Map(topology.integration_solutions.map((solution) => [solution.id, solution.name]));
  const pathByStreamId = new Map(topology.delivery_paths.map((path) => [path.stream_id, path]));
  return pair.streamIds
    .map((streamId) => topology.data_streams.find((stream) => stream.id === streamId))
    .filter((stream): stream is BusinessDataStream => Boolean(stream))
    .map((stream) => ({
      id: stream.id,
      sourceSystemId: stream.source_system_id,
      destinationSystemId: stream.destination_system_id,
      name: stream.name,
      summary: stream.business_summary || stream.description,
      businessObjects: stream.business_object_ids.map((id) => objectById.get(id) ?? id),
      integrationSolutions: stream.integration_solution_ids.map((id) => solutionById.get(id) ?? id),
      protocols: streamProtocols(stream),
      status: relationshipStatus([stream.status]),
      artifactIds: stream.artifact_ids,
      evidenceIds: stream.evidence_ids,
      deliveryPath: pathByStreamId.get(stream.id),
      transformations: topology.transformations.filter((transformation) => transformation.stream_ids.includes(stream.id)),
    }));
}

/** Exact integration nodes involved in an L1 relationship; a renderer can
 * insert these as intermediates in an optional Architecture routing mode. */
export function relationshipIntegrationNodes(
  relationship: ArchitectureRelationship,
): string[] {
  return unique(relationship.integrationSolutionIds.map(integrationNodeId));
}
