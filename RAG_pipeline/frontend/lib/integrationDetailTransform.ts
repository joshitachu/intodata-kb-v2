import type { Edge, Node } from "@xyflow/react";
import type {
  LandscapeChannel,
  LandscapeDiagnostic,
  LandscapeInterface,
  LandscapeProvenance,
  ProjectLandscapeResponse,
} from "@/types/landscape";
import { landscapeEntityLabel, type TopologyEdge } from "@/lib/landscapeTransform";

export interface IntegrationDetail {
  edge: TopologyEdge;
  provenance: LandscapeProvenance[];
  channels: LandscapeChannel[];
  interfaces: LandscapeInterface[];
  diagnostics: LandscapeDiagnostic[];
  unmapped: Record<string, unknown>[];
  nodes: Node[];
  edges: Edge[];
}

export interface ImplementingDesignMapping {
  source: string;
  target: string;
  mappingType: "direct" | "value" | "derived" | "constant" | "unknown";
  origin: "business_topology" | "scanner_lineage";
  group: string;
}

export interface ImplementingDesignDetail {
  record: LandscapeProvenance;
  label: string;
  artifactKind: string;
  artifactRole: string;
  businessRole: string;
  description: string;
  integrationSolutions: string[];
  protocols: string[];
  channels: LandscapeChannel[];
  interfaces: LandscapeInterface[];
  deliverySegments: Array<{
    id: string;
    name: string;
    protocol: string;
    component: string;
    resource: string;
  }>;
  invokedDesigns: string[];
  mappingComponents: string[];
  mappings: ImplementingDesignMapping[];
  mappingAttribution: "exact" | "ambiguous" | "none";
}

function evidenceMatches(
  evidence: { qualified_artifact_id: string },
  provenance: LandscapeProvenance[],
): boolean {
  return provenance.some((record) => record.qualified_artifact_id === evidence.qualified_artifact_id);
}

function artifactEvidenceForRecord(
  landscape: ProjectLandscapeResponse,
  record: LandscapeProvenance,
) {
  const candidates = (landscape.topology_evidence?.artifacts ?? []).filter(
    (artifact) => artifact.name === record.artifact_id || artifact.job_id === record.artifact_id,
  );
  const exact = candidates.filter((artifact) => artifact.source_root === record.source_root);
  return exact.length > 0 ? exact : candidates.length === 1 ? candidates : [];
}

/**
 * Builds the business-readable explanation shown before opening a technical
 * job graph. Mapping attribution is deliberately exact: stream-level mappings
 * must reference this artifact, while scanner lineage is used only when the
 * job name resolves to one source-root identity.
 */
export function implementingDesignDetail(
  landscape: ProjectLandscapeResponse,
  edge: TopologyEdge,
  record: LandscapeProvenance,
): ImplementingDesignDetail {
  const topology = landscape.business_topology;
  const evidenceArtifacts = artifactEvidenceForRecord(landscape, record);
  const sourceNames = new Set([
    record.artifact_id,
    ...evidenceArtifacts.flatMap((artifact) => [artifact.name, artifact.job_id]),
  ]);
  const relations = (landscape.topology_evidence?.relations ?? []).filter(
    (relation) => sourceNames.has(relation.source),
  );
  const invokedDesigns = [...new Set(relations.map((relation) => relation.target).filter(Boolean))];
  const relatedArtifacts = invokedDesigns.flatMap((designName) => {
    const candidates = (landscape.topology_evidence?.artifacts ?? []).filter(
      (artifact) => artifact.name === designName || artifact.job_id === designName,
    );
    const sameRoot = candidates.filter((artifact) => artifact.source_root === record.source_root);
    return sameRoot.length === 1 ? sameRoot : candidates.length === 1 ? candidates : [];
  });
  const relevantArtifacts = [...evidenceArtifacts, ...relatedArtifacts];
  const evidenceArtifactIds = new Set(relevantArtifacts.map((artifact) => artifact.id));
  const lineageJobs = new Set([record.artifact_id, ...invokedDesigns]);
  const ambiguousLineageJobs = new Set(
    [...lineageJobs].filter((jobName) => {
      const roots = new Set(
        (landscape.topology_evidence?.artifacts ?? [])
          .filter((artifact) => artifact.name === jobName || artifact.job_id === jobName)
          .map((artifact) => artifact.source_root),
      );
      return roots.size > 1;
    }),
  );
  const stream = edge.businessStream;
  const transformations = (topology?.transformations ?? []).filter(
    (transformation) =>
      Boolean(stream && transformation.stream_ids.includes(stream.id))
      && transformation.artifact_ids.some((artifactId) => evidenceArtifactIds.has(artifactId)),
  );
  const mappings: ImplementingDesignMapping[] = transformations.flatMap((transformation) =>
    transformation.mappings.map((mapping) => ({
      source: mapping.source,
      target: mapping.target,
      mappingType: mapping.mapping_type,
      origin: "business_topology" as const,
      group: transformation.name || "Known transformation",
    })),
  );
  if (ambiguousLineageJobs.size < lineageJobs.size) {
    for (const lineage of landscape.topology_evidence?.lineage ?? []) {
      if (!lineageJobs.has(lineage.job_name) || ambiguousLineageJobs.has(lineage.job_name) || !lineage.source_column || !lineage.target_column) continue;
      mappings.push({
        source: [lineage.source_table, lineage.source_column].filter(Boolean).join("."),
        target: [lineage.target_table, lineage.target_column].filter(Boolean).join("."),
        mappingType: "direct",
        origin: "scanner_lineage",
        group: lineage.job_name === record.artifact_id ? "Scanner field lineage" : `Invoked job · ${lineage.job_name}`,
      });
    }
  }
  const deduplicatedMappings = [...new Map(
    mappings.map((mapping) => [`${mapping.source}\u0000${mapping.target}\u0000${mapping.mappingType}`, mapping]),
  ).values()];
  const annotations = (topology?.artifact_annotations ?? []).filter((annotation) =>
    evidenceArtifactIds.has(annotation.artifact_id),
  );
  const integrationSolutions = (topology?.integration_solutions ?? []).filter((solution) =>
    solution.artifact_ids.some((artifactId) => evidenceArtifactIds.has(artifactId)),
  );
  const path = stream?.delivery_path_id
    ? topology?.delivery_paths.find((item) => item.id === stream.delivery_path_id)
    : undefined;
  const deliverySegments = (path?.segments ?? []).filter((segment) =>
    segment.artifact_ids.some((artifactId) => evidenceArtifactIds.has(artifactId))
    || segment.component === record.artifact_id,
  );
  const channels = landscape.channels.filter(
    (channel) => channel.qualified_artifact_id === record.qualified_artifact_id,
  );
  const interfaces = landscape.interfaces.filter(
    (item) => item.qualified_artifact_id === record.qualified_artifact_id,
  );
  const artifact = evidenceArtifacts[0];
  const mappingComponents = (landscape.topology_evidence?.components ?? [])
    .filter((component) => evidenceArtifactIds.has(component.artifact_id))
    .filter((component) => /(map|transform|xml|java|convert|extract)/i.test(component.component_type))
    .map((component) => `${component.name} (${component.component_type})`);
  return {
    record,
    label: artifact?.name || artifact?.job_id || record.artifact_id,
    artifactKind: artifact?.artifact_kind || "design",
    artifactRole: artifact?.artifact_role || "implementation",
    businessRole: annotations.map((annotation) => annotation.business_role).filter(Boolean).join(" · "),
    description:
      annotations.map((annotation) => annotation.description).filter(Boolean).join(" ")
      || stream?.business_summary
      || "Scanner evidence implementing this data stream.",
    integrationSolutions: integrationSolutions.length > 0
      ? integrationSolutions.map((solution) => solution.name)
      : (stream?.integration_solution_ids ?? [])
        .map((id) => topology?.integration_solutions.find((solution) => solution.id === id)?.name)
        .filter((name): name is string => Boolean(name)),
    protocols: stream?.protocols ?? [],
    channels,
    interfaces,
    deliverySegments: deliverySegments.map((segment) => ({
      id: segment.id,
      name: segment.name,
      protocol: segment.protocol,
      component: segment.component,
      resource: segment.resource,
    })),
    invokedDesigns,
    mappingComponents: [...new Set(mappingComponents)],
    mappings: deduplicatedMappings,
    mappingAttribution: deduplicatedMappings.length > 0
      ? "exact"
      : ambiguousLineageJobs.size > 0
        ? "ambiguous"
        : "none",
  };
}

/**
 * Builds L2 only from an L1 edge's exact provenance.  In particular it does
 * not join channels by name, so redacted/place-holder values can never create
 * an invented producer/consumer relationship.
 */
export function integrationDetailForEdge(
  landscape: ProjectLandscapeResponse,
  edge: TopologyEdge,
): IntegrationDetail {
  const provenance = edge.provenance;
  const channels = landscape.channels.filter((channel) => evidenceMatches(channel, provenance));
  const interfaces = landscape.interfaces.filter((item) => evidenceMatches(item, provenance));
  const artifactIds = new Set(provenance.map((item) => item.qualified_artifact_id));
  const diagnostics = landscape.diagnostics.filter((diagnostic) =>
    diagnostic.evidence.some((id) => artifactIds.has(id)),
  );
  const sourceNode: Node = {
    id: "boundary:source", type: "integrationBoundary", position: { x: 0, y: 160 },
    data: { label: landscapeEntityLabel(landscape, edge.source), side: "source", edge }, width: 220, height: 96,
  };
  const targetNode: Node = {
    id: "boundary:target", type: "integrationBoundary", position: { x: 740, y: 160 },
    data: { label: landscapeEntityLabel(landscape, edge.target), side: "target", edge }, width: 220, height: 96,
  };
  const uniqueProvenance = new Map<string, LandscapeProvenance>();
  for (const record of provenance) {
    uniqueProvenance.set(record.qualified_artifact_id, record);
  }
  const artifactNodes: Node[] = [...uniqueProvenance.values()].map((record, index) => ({
    id: `artifact:${record.qualified_artifact_id}`,
    type: "integrationArtifact",
    position: { x: 350, y: 36 + index * 126 },
    data: { record },
    width: 280,
    height: 106,
  }));
  // What the stream carries: prefer named business objects, else the stream's
  // own label. Shown on the inbound edge so the flow reads
  // source --(payload)--> design --> target.
  const stream = edge.businessStream;
  const objectNames = (stream?.business_object_ids ?? [])
    .map((id) => landscape.business_topology?.business_objects.find((object) => object.id === id)?.name)
    .filter((name): name is string => Boolean(name));
  const payloadLabel = objectNames.join(", ") || stream?.name || "";
  const edgeStyle = { stroke: "#0891b2", strokeWidth: 1.8 } as const;
  const marker = { type: "arrowclosed" as const, color: "#0891b2", width: 16, height: 16 };
  const labelStyle = { fill: "#155e75", fontSize: 11, fontWeight: 650 };
  const labelBgStyle = { fill: "#ffffff", fillOpacity: 0.92 };
  const flowEdges: Edge[] = [...uniqueProvenance.values()].flatMap((record) => [
    { id: `source:${record.qualified_artifact_id}`, source: "boundary:source", target: `artifact:${record.qualified_artifact_id}`, type: "smoothstep", markerEnd: marker, style: edgeStyle, label: payloadLabel || undefined, labelStyle, labelBgStyle, labelBgPadding: [6, 3] as [number, number] },
    { id: `target:${record.qualified_artifact_id}`, source: `artifact:${record.qualified_artifact_id}`, target: "boundary:target", type: "smoothstep", markerEnd: marker, style: edgeStyle },
  ]);
  return { edge, provenance, channels, interfaces, diagnostics, unmapped: landscape.unmapped, nodes: [sourceNode, ...artifactNodes, targetNode], edges: flowEdges };
}

export function groupEvidenceRecords<T extends { qualified_artifact_id: string }>(records: T[]): Array<{ key: string; records: T[] }> {
  const grouped = new Map<string, T[]>();
  for (const record of records) {
    const values = grouped.get(record.qualified_artifact_id) ?? [];
    values.push(record);
    grouped.set(record.qualified_artifact_id, values);
  }
  return [...grouped].map(([key, groupedRecords]) => ({ key, records: groupedRecords }));
}
