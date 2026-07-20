import { z } from "zod";

// Field/column lineage query contract (S4 API / S5 UI), verified against the
// actual backend implementation
// (knowledge_base/mcp-server/src/mcp_server/schemas.py::LineageFieldResponse
// and services/lineage_service.py) rather than the architecture doc's prose
// alone.
//
// GET /mcp/projects/{id}/lineage/field?job=&table=&column=&component=&direction=&max_depth=
//
// Real-data constraints that shape this contract (see lineage_service.py's
// module docstring):
// - Lineage does NOT cross job boundaries yet (cross-job stitching is a
//   later scanner-side increment) - every node/edge in one response belongs
//   to the same `job_name` as the query. The frontend must not fabricate
//   cross-job hops; job-boundary highlighting degrades gracefully to "the
//   one job this response is scoped to" until the backend contract grows a
//   per-edge job identity.
// - `resolved: false` entries (constant/dynamic-only) still carry the edge
//   (empty source_table/source_column) rather than being dropped - the UI
//   must show these as unresolved, never invent a source for them.
// - `confidence` widens to certain|likely|dynamic|opaque but is a plain
//   string on the wire (LineageConfidence enum lives in intodoc.models) -
//   kept as a bare string here, same convention as ComponentGraphEdgeSchema,
//   so an unrecognized future value is never rejected outright.

export const LINEAGE_DIRECTIONS = ["up", "down", "both"] as const;
export const LineageDirectionSchema = z.enum(LINEAGE_DIRECTIONS);
export type LineageDirection = z.infer<typeof LineageDirectionSchema>;

// Confidence values that mean "a real, statically-resolved reference" -
// never applied to dynamic/opaque hops, which must always read as such
// (root CLAUDE.md rule: never present derived/uncertain data as fact).
export const LINEAGE_RESOLVED_CONFIDENCE_VALUES: readonly string[] = ["certain", "likely"];
export const LINEAGE_CONFIDENCE_ORDER: readonly string[] = [
  "certain",
  "likely",
  "dynamic",
  "opaque",
];

export const LineageTransformationStepSchema = z.object({
  transformation_type: z.string().optional().default(""),
  expression: z.string().optional().default(""),
  human_explanation: z.string().optional().default(""),
});
export type LineageTransformationStep = z.infer<typeof LineageTransformationStepSchema>;

export const LineageFieldNodeSchema = z.object({
  job_name: z.string(),
  table: z.string(),
  column: z.string(),
  component: z.string().optional().default(""),
});
export type LineageFieldNode = z.infer<typeof LineageFieldNodeSchema>;

export const LineageFieldEdgeSchema = z.object({
  job_name: z.string(),
  source_table: z.string().optional().default(""),
  source_column: z.string().optional().default(""),
  source_component: z.string().optional().default(""),
  target_table: z.string(),
  target_column: z.string(),
  target_component: z.string().optional().default(""),
  // Bare string, not a strict enum - see module note above.
  confidence: z.string().optional().default("opaque"),
  resolved: z.boolean().optional().default(false),
  dynamic_refs: z.array(z.string()).optional().default([]),
  transformations: z.array(LineageTransformationStepSchema).optional().default([]),
});
export type LineageFieldEdge = z.infer<typeof LineageFieldEdgeSchema>;

export const LineageFieldResponseSchema = z.object({
  project_id: z.string(),
  job_name: z.string(),
  start: LineageFieldNodeSchema,
  direction: LineageDirectionSchema,
  max_depth: z.number(),
  nodes: z.array(LineageFieldNodeSchema).optional().default([]),
  edges: z.array(LineageFieldEdgeSchema).optional().default([]),
  truncated: z.boolean().optional().default(false),
});
export type LineageFieldResponse = z.infer<typeof LineageFieldResponseSchema>;

// One field-lineage query, as issued from a level-3 column click (or, later,
// a field chip elsewhere once one exists - see lib/lineageHighlight.ts).
export interface FieldLineageQuery {
  jobName: string;
  table: string;
  column: string;
  component?: string;
  direction?: LineageDirection;
  maxDepth?: number;
}
