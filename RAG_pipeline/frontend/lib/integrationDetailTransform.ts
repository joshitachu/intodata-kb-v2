import type { Edge, Node } from "@xyflow/react";
import type {
  LandscapeChannel,
  LandscapeDiagnostic,
  LandscapeEdge,
  LandscapeInterface,
  LandscapeProvenance,
  ProjectLandscapeResponse,
} from "@/types/landscape";
import { landscapeEntityLabel } from "@/lib/landscapeTransform";

export interface IntegrationDetail {
  edge: LandscapeEdge;
  provenance: LandscapeProvenance[];
  channels: LandscapeChannel[];
  interfaces: LandscapeInterface[];
  diagnostics: LandscapeDiagnostic[];
  unmapped: Record<string, unknown>[];
  nodes: Node[];
  edges: Edge[];
}

function evidenceMatches(
  evidence: { qualified_artifact_id: string },
  provenance: LandscapeProvenance[],
): boolean {
  return provenance.some((record) => record.qualified_artifact_id === evidence.qualified_artifact_id);
}

/**
 * Builds L2 only from an L1 edge's exact provenance.  In particular it does
 * not join channels by name, so redacted/place-holder values can never create
 * an invented producer/consumer relationship.
 */
export function integrationDetailForEdge(
  landscape: ProjectLandscapeResponse,
  edge: LandscapeEdge,
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
  const artifactNodes: Node[] = provenance.map((record, index) => ({
    id: `artifact:${record.qualified_artifact_id}`,
    type: "integrationArtifact",
    position: { x: 350, y: 36 + index * 126 },
    data: { record }, width: 260, height: 92,
  }));
  const flowEdges: Edge[] = provenance.flatMap((record) => [
    { id: `source:${record.qualified_artifact_id}`, source: "boundary:source", target: `artifact:${record.qualified_artifact_id}`, type: "smoothstep", markerEnd: { type: "arrowclosed" as const } },
    { id: `target:${record.qualified_artifact_id}`, source: `artifact:${record.qualified_artifact_id}`, target: "boundary:target", type: "smoothstep", markerEnd: { type: "arrowclosed" as const } },
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
