import type {
  BusinessConflict,
  BusinessDataStream,
  BusinessIntegrationSolution,
  BusinessObject,
  BusinessRisk,
  BusinessRule,
  BusinessSystem,
  BusinessTopology,
  BusinessDeliveryPath,
  BusinessTransformation,
  BusinessUnknown,
  ContractWidth,
  DeploymentAgreement,
  RuntimeObservation,
  StreamCondition,
  TransformationMapping,
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

/**
 * The additive schema slots are `.optional()` so an older payload still parses
 * (see `types/landscape.ts`). This projection is the ONE place that resolves
 * them to a definite value, so no renderer has to decide what a missing
 * `lifecycle` means.
 */
export type TransformationKind = NonNullable<BusinessTransformation["kind"]>;
export type BusinessRuleKind = NonNullable<BusinessRule["kind"]>;
export type StreamConditionKind = NonNullable<StreamCondition["kind"]>;
export type ReachesTarget = NonNullable<TransformationMapping["reaches_target"]>;
export type StreamDirectionPattern = NonNullable<BusinessDataStream["direction_pattern"]>;
export type StreamLifecycle = NonNullable<BusinessDataStream["lifecycle"]>;

/**
 * One field-level mapping row, flattened out of its owning transformation but
 * keeping that transformation's identity and kind. A row is the unit a reader
 * scans; the transformation is the unit that says what kind of fact it is.
 */
export interface StreamFieldRow {
  /** 1-based position within this stream's field table, assigned once here so
   * the panel and the full table number the same row identically. */
  index: number;
  transformationId: string;
  transformationName: string;
  transformationKind: TransformationKind;
  targetField: string;
  source: string;
  expression: string;
  /** What to show in one column: the verbatim rule when there is one. */
  sourceOrExpression: string;
  mappingType: NonNullable<TransformationMapping["mapping_type"]>;
  category: string;
  reachesTarget: ReachesTarget;
  note: string;
}

/** Value-domain transformations collected under the target field they define. */
export interface StreamValueDomainGroup {
  targetField: string;
  transformations: BusinessTransformation[];
}

/** One measured number with the observation that reported it. */
export interface StreamRuntimeMetricRow {
  id: string;
  label: string;
  value: string;
  unit: string;
  window: string;
  source: string;
  note: string;
  observationId: string;
  observationName: string;
  observationKind: NonNullable<RuntimeObservation["kind"]>;
}

/**
 * Everything structured that is attached to ONE data stream, resolved from the
 * topology by id in exactly one place.
 *
 * Transformations are partitioned by `kind`, never merged: a field mapping, a
 * value domain and a derived rule are three different kinds of fact, and a
 * status value table flat-listed beside field mappings reads as a business
 * decision. Transformations the producer did not classify stay in
 * `unclassifiedTransformations` -- they are NOT promoted into a classified
 * bucket, because inventing a classification is the same defect in reverse.
 */
export interface StreamStructuredFacts {
  fieldMappings: BusinessTransformation[];
  valueDomains: BusinessTransformation[];
  derivedRules: BusinessTransformation[];
  constantSets: BusinessTransformation[];
  unclassifiedTransformations: BusinessTransformation[];
  fieldRows: StreamFieldRow[];
  /** Header/body/code steps of a message route: not field mappings. */
  messageRows: StreamFieldRow[];
  valueDomainGroups: StreamValueDomainGroup[];
  businessRules: BusinessRule[];
  runtimeObservations: RuntimeObservation[];
  runtimeMetrics: StreamRuntimeMetricRow[];
  risks: BusinessRisk[];
  unknowns: BusinessUnknown[];
  conflicts: BusinessConflict[];
}

export interface ArchitectureFlowProjection extends StreamStructuredFacts {
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
  /** Structured stream facts that were previously concatenated into prose. */
  mechanism: string;
  generation: string;
  directionPattern: StreamDirectionPattern;
  lifecycle: StreamLifecycle;
  contractWidth: ContractWidth | null;
  conditions: StreamCondition[];
  deployment: DeploymentAgreement | null;
  keyForm: string;
  verdict: string;
  documentRefs: string[];
}

/**
 * Each vocabulary below has ONE audience, and its language follows that
 * audience. This one heads the mapping groups in the L2 design panel, which is
 * English alongside "scanner lineage" / "structured transformation".
 */
export const TRANSFORMATION_KIND_LABEL: Record<TransformationKind, string> = {
  field_mapping: "Field mapping",
  value_domain: "Value domain",
  derived_rule: "Derived rule",
  constant_set: "Constant set",
  unknown: "Unclassified",
};

/** Client-facing (Dutch) vocabulary: rendered only in the business panels. */
export const BUSINESS_RULE_KIND_LABEL: Record<BusinessRuleKind, string> = {
  value_mapping: "Waardevertaling",
  precedence: "Voorrang",
  derivation: "Afleiding",
  selection: "Selectie",
  feedback: "Terugkoppeling",
  ordering: "Volgorde",
  disabled: "Uitgeschakeld",
  threshold: "Drempel",
  unknown: "Niet geclassificeerd",
};

/** Client-facing (Dutch) vocabulary: rendered only in the business panels. */
export const STREAM_CONDITION_KIND_LABEL: Record<StreamConditionKind, string> = {
  trigger: "Trigger",
  filter: "Filter",
  ordering: "Volgorde",
  schedule: "Planning",
  disabled: "Uitgeschakeld",
  guard: "Bewaking",
  unknown: "Niet geclassificeerd",
};

/** Client-facing (Dutch) vocabulary: rendered only in the business panels. */
export const REACHES_TARGET_LABEL: Record<ReachesTarget, string> = {
  yes: "Bereikt doel",
  no: "Niet gelezen",
  partial: "Deels",
  disabled: "Uitgeschakeld",
  unknown: "Niet vastgesteld",
};

/** Field rows are emitted in this kind order so numbering is deterministic. */
// A field mapping and a message-construction step are not the same claim, and a
// reader cannot tell them apart once they share a heading. A Camel route has no
// tMap: it sets message headers, fills the body and runs its own code. Counting
// those as "Velden" reports 48 field mappings for a design that has none.
const FIELD_ROW_KIND_ORDER: TransformationKind[] = ["field_mapping", "constant_set"];
const MESSAGE_ROW_KIND_ORDER: TransformationKind[] = ["derived_rule"];

function fieldRowsFor(
  byKind: Map<TransformationKind, BusinessTransformation[]>,
  order: TransformationKind[] = FIELD_ROW_KIND_ORDER,
): StreamFieldRow[] {
  const rows: StreamFieldRow[] = [];
  for (const kind of order) {
    for (const transformation of byKind.get(kind) ?? []) {
      for (const mapping of transformation.mappings) {
        rows.push({
          index: rows.length + 1,
          transformationId: transformation.id,
          transformationName: transformation.name,
          transformationKind: kind,
          targetField: mapping.target || transformation.target_field || "",
          source: mapping.source,
          expression: mapping.expression ?? "",
          // The producer's plain-language explanation wins over the verbatim
          // expression. Both are published; only one fits on a 390px row, and a
          // reader who needs `SELECT count(*) AS elements ${exchangeProperty.from}`
          // needs it as evidence under the sentence, not instead of it.
          sourceOrExpression: mapping.note || mapping.expression || mapping.source,
          mappingType: mapping.mapping_type ?? "unknown",
          category: mapping.category ?? "",
          reachesTarget: mapping.reaches_target ?? "unknown",
          note: mapping.note ?? "",
        });
      }
    }
  }
  return rows;
}

function valueDomainGroupsFor(valueDomains: BusinessTransformation[]): StreamValueDomainGroup[] {
  const groups = new Map<string, BusinessTransformation[]>();
  for (const transformation of valueDomains) {
    // A value table without a declared target field is still shown, under its
    // own name -- dropping it would hide evidence the producer did supply.
    const key = transformation.target_field || transformation.name || transformation.id;
    groups.set(key, [...(groups.get(key) ?? []), transformation]);
  }
  return [...groups.entries()].map(([targetField, transformations]) => ({ targetField, transformations }));
}

function runtimeMetricRows(observations: RuntimeObservation[]): StreamRuntimeMetricRow[] {
  return observations.flatMap((observation) =>
    (observation.metrics ?? []).map((metric, index) => ({
      id: `${observation.id}:${index}`,
      label: metric.label,
      value: metric.value,
      unit: metric.unit,
      window: metric.window || observation.observation_window,
      source: metric.source || observation.source || "",
      note: metric.note,
      observationId: observation.id,
      observationName: observation.name,
      observationKind: observation.kind ?? "unknown",
    })),
  );
}

/**
 * Resolves every structured fact attached to one stream. The single resolution
 * point for both the L1 flow panel and the L2 detail tabs: two lookups of the
 * same relationship are two rules free to drift apart.
 */
export function streamStructuredFacts(
  topology: BusinessTopology,
  stream: BusinessDataStream,
): StreamStructuredFacts {
  const transformations = topology.transformations.filter((transformation) =>
    transformation.stream_ids.includes(stream.id),
  );
  const byKind = new Map<TransformationKind, BusinessTransformation[]>();
  for (const transformation of transformations) {
    const kind = transformation.kind ?? "unknown";
    byKind.set(kind, [...(byKind.get(kind) ?? []), transformation]);
  }
  const transformationIds = new Set(transformations.map((transformation) => transformation.id));
  const businessRules = (topology.business_rules ?? []).filter((rule) =>
    rule.stream_ids.includes(stream.id)
    || rule.transformation_ids.some((id) => transformationIds.has(id)),
  );
  const observationIds = new Set(stream.runtime_observation_ids ?? []);
  const runtimeObservations = (topology.runtime_observations ?? []).filter((observation) =>
    observationIds.has(observation.id) || observation.stream_ids.includes(stream.id),
  );
  const riskIds = new Set(stream.risk_ids);
  const unknownIds = new Set(stream.unknown_ids);
  const conflictIds = new Set(stream.conflict_ids ?? []);
  const valueDomains = byKind.get("value_domain") ?? [];
  return {
    fieldMappings: byKind.get("field_mapping") ?? [],
    valueDomains,
    derivedRules: byKind.get("derived_rule") ?? [],
    constantSets: byKind.get("constant_set") ?? [],
    unclassifiedTransformations: byKind.get("unknown") ?? [],
    fieldRows: fieldRowsFor(byKind),
    messageRows: fieldRowsFor(byKind, MESSAGE_ROW_KIND_ORDER),
    valueDomainGroups: valueDomainGroupsFor(valueDomains),
    businessRules,
    runtimeObservations,
    runtimeMetrics: runtimeMetricRows(runtimeObservations),
    risks: topology.risks.filter((risk) => riskIds.has(risk.id) || risk.stream_ids.includes(stream.id)),
    unknowns: topology.unknowns.filter((unknown) => unknownIds.has(unknown.id) || unknown.stream_ids.includes(stream.id)),
    conflicts: (topology.conflicts ?? []).filter((conflict) =>
      conflictIds.has(conflict.id) || conflict.subject_refs.includes(stream.id),
    ),
  };
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

/**
 * The canvas node id this artifact system is drawn as.
 *
 * Exported because the assistant's emphasis is computed from ARTIFACT ids
 * (`sys-afas`) while this canvas draws NODE ids (`system:sys-afas`, or the
 * collapsed supporting group). Without a shared mapping the emphasis silently
 * matched nothing, and a non-empty emphasis map with no matches dims the entire
 * diagram -- the answer named AFAS and AFAS was the one thing greyed out.
 */
export function architectureSystemNodeId(systemId: string): string {
  return systemNodeId(systemId);
}

/** The unordered pair-edge id for two NODE ids, as `architectureRelationshipPairs` mints it. */
export function architecturePairId(first: string, second: string): string {
  const endpoints = [first, second].sort();
  return `pair:${endpoints[0]}<->${endpoints[1]}`;
}

function topologyTitle(topology: BusinessTopology): string {
  const project = topology.metadata.project_id
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toLocaleUpperCase());
  return `${project || "Company"} architecture`;
}

function systemDegrees(topology: BusinessTopology): Map<string, { incoming: number; outgoing: number }> {
  const degrees = new Map(topology.systems.map((system) => [system.id, { incoming: 0, outgoing: 0 }]));
  for (const stream of topology.data_streams) {
    const source = degrees.get(stream.source_system_id);
    const destination = degrees.get(stream.destination_system_id);
    if (source) source.outgoing += 1;
    if (destination) destination.incoming += 1;
  }
  return degrees;
}

function selectAnchorSystemId(topology: BusinessTopology): string | null {
  const degrees = systemDegrees(topology);
  const maximo = degrees.get("sys-maximo");
  if (maximo && maximo.incoming + maximo.outgoing > 0) return "sys-maximo";

  return [...topology.systems]
    .filter((system) => !INTEGRATION_SYSTEM_IDS.has(system.id) && !SUPPORTING_SYSTEM_IDS.has(system.id))
    .sort((left, right) => {
      const leftDegree = degrees.get(left.id) ?? { incoming: 0, outgoing: 0 };
      const rightDegree = degrees.get(right.id) ?? { incoming: 0, outgoing: 0 };
      const leftScore = (leftDegree.incoming + leftDegree.outgoing) * 10 + Math.min(leftDegree.incoming, leftDegree.outgoing);
      const rightScore = (rightDegree.incoming + rightDegree.outgoing) * 10 + Math.min(rightDegree.incoming, rightDegree.outgoing);
      return rightScore - leftScore || left.name.localeCompare(right.name);
    })[0]?.id ?? null;
}

function reachableDistances(topology: BusinessTopology, anchorId: string, reverse: boolean): Map<string, number> {
  const adjacency = new Map<string, Set<string>>();
  for (const stream of topology.data_streams) {
    const source = reverse ? stream.destination_system_id : stream.source_system_id;
    const destination = reverse ? stream.source_system_id : stream.destination_system_id;
    adjacency.set(source, new Set([...(adjacency.get(source) ?? []), destination]));
  }
  const distances = new Map([[anchorId, 0]]);
  const queue = [anchorId];
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const next of adjacency.get(current) ?? []) {
      if (distances.has(next)) continue;
      distances.set(next, distances.get(current)! + 1);
      queue.push(next);
    }
  }
  return distances;
}

function topologyColumns(topology: BusinessTopology, anchorId: string | null): Map<string, ArchitectureColumn> {
  if (!anchorId) return new Map();
  if (anchorId === "sys-maximo") return new Map(Object.entries(WERKORDER_COLUMNS));

  const degrees = systemDegrees(topology);
  const fromAnchor = reachableDistances(topology, anchorId, false);
  const toAnchor = reachableDistances(topology, anchorId, true);
  const directToAnchor = new Map<string, number>();
  const directFromAnchor = new Map<string, number>();
  for (const stream of topology.data_streams) {
    if (stream.destination_system_id === anchorId) directToAnchor.set(stream.source_system_id, (directToAnchor.get(stream.source_system_id) ?? 0) + 1);
    if (stream.source_system_id === anchorId) directFromAnchor.set(stream.destination_system_id, (directFromAnchor.get(stream.destination_system_id) ?? 0) + 1);
  }

  const columns = new Map<string, ArchitectureColumn>([[anchorId, "anchor"]]);
  for (const system of topology.systems) {
    if (system.id === anchorId) continue;
    if (SUPPORTING_SYSTEM_IDS.has(system.id)) {
      columns.set(system.id, "supporting");
      continue;
    }
    const toward = toAnchor.get(system.id);
    const away = fromAnchor.get(system.id);
    const intoAnchor = directToAnchor.get(system.id) ?? 0;
    const outOfAnchor = directFromAnchor.get(system.id) ?? 0;
    const degree = degrees.get(system.id) ?? { incoming: 0, outgoing: 0 };
    if (intoAnchor > outOfAnchor || (toward !== undefined && away === undefined)) {
      columns.set(system.id, "upstream");
    } else if (away === 1 || outOfAnchor > 0) {
      columns.set(system.id, "downstream");
    } else if (away !== undefined || degree.incoming > 0) {
      columns.set(system.id, "secondary");
    } else if (degree.outgoing > 0) {
      columns.set(system.id, "upstream");
    } else {
      columns.set(system.id, "supporting");
    }
  }
  return columns;
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
export function normalizeArchitectureL1(
  topology: BusinessTopology,
  options: { maxVisibleRelationships?: number } = {},
): ArchitectureL1ViewModel {
  // The cap keeps the default map quiet on a wide estate. It is a parameter
  // rather than a constant because the omission was previously silent: the
  // measured estate hid 10 of 28 relationships, `hiddenRelationshipIds` had no
  // consumer, and a whole system family was therefore invisible with nothing
  // on screen saying so.
  const maxVisibleRelationships = Math.max(
    1,
    options.maxVisibleRelationships ?? MAX_VISIBLE_RELATIONSHIPS,
  );
  const systemById = new Map(topology.systems.map((system) => [system.id, system]));
  const objectById = new Map(topology.business_objects.map((object) => [object.id, object]));
  const anchorSystemId = selectAnchorSystemId(topology);
  const columns = topologyColumns(topology, anchorSystemId);

  const nodes = new Map<string, ArchitectureNode>();
  for (const system of topology.systems) {
    if (INTEGRATION_SYSTEM_IDS.has(system.id)) continue;
    const id = systemNodeId(system.id);
    if (id === SUPPORTING_GROUP_ID) continue;
    const column = columns.get(system.id) ?? "supporting";
    const layer: ArchitectureLayer = system.id === anchorSystemId || CORE_SYSTEM_IDS.has(system.id)
      ? "core"
      : column === "supporting" ? "supporting" : "external";
    nodes.set(id, {
      id,
      label: system.id === "sys-activemq" ? "ActiveMQ" : system.name,
      description: titleFromSystem(system),
      layer,
      column,
      kind: system.id === "sys-activemq" ? "broker" : "system",
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
  const visibleRelationships = sorted.slice(0, maxVisibleRelationships);
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
  // An evidence-bounded topology may deliberately have no directional
  // streams. Keep its non-integration systems visible in that state so a
  // renderer never receives an empty-but-valid architecture model.
  const hasVisibleRelationships = visibleNodeIds.size > 0;
  const visibleNodes = [...nodes.values()]
    .filter((node) => hasVisibleRelationships
      ? visibleNodeIds.has(node.id) || node.layer === "integration" || node.kind === "broker"
      : node.layer !== "integration")
    .sort((a, b) => a.layer.localeCompare(b.layer) || b.degree - a.degree || a.label.localeCompare(b.label));

  return {
    level: "L1",
    title: topologyTitle(topology),
    anchorId: anchorSystemId ? systemNodeId(anchorSystemId) : "",
    source: "business_topology",
    nodes: visibleNodes,
    relationships: visibleRelationships,
    hiddenRelationshipIds: sorted.slice(maxVisibleRelationships).map((relationship) => relationship.id),
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
    const id = architecturePairId(relationship.source, relationship.target);
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
      mechanism: stream.mechanism ?? "",
      generation: stream.generation ?? "",
      directionPattern: stream.direction_pattern ?? "unknown",
      lifecycle: stream.lifecycle ?? "unknown",
      contractWidth: stream.contract_width ?? null,
      conditions: stream.conditions ?? [],
      deployment: stream.deployment ?? null,
      keyForm: stream.key_form ?? "",
      verdict: stream.verdict ?? "",
      documentRefs: stream.document_refs ?? [],
      ...streamStructuredFacts(topology, stream),
    }));
}

/** Exact integration nodes involved in an L1 relationship; a renderer can
 * insert these as intermediates in an optional Architecture routing mode. */
export function relationshipIntegrationNodes(
  relationship: ArchitectureRelationship,
): string[] {
  return unique(relationship.integrationSolutionIds.map(integrationNodeId));
}
