import { z } from "zod";

// Level-2 topology drill-down contract (S1).
// GET /mcp/projects/{id}/jobs/{job_name}/graph -> JobGraphResponse
// Documented in RAG_pipeline/docs/topology-lineage-runtime-architecture.md (section 2.1).

export const ComponentRoleSchema = z.enum([
  "input",
  "transform",
  "output",
  "orchestration",
  "logging",
]);
export type ComponentRole = z.infer<typeof ComponentRoleSchema>;

// param_summary is bounded and credential-filtered server-side; values are
// scalar display values, never secrets.
export const ParamValueSchema = z.union([
  z.string(),
  z.number(),
  z.boolean(),
  z.null(),
]);
export type ParamValue = z.infer<typeof ParamValueSchema>;

export const JobGraphNodeSchema = z.object({
  unique_name: z.string(),
  component_type: z.string(),
  role: ComponentRoleSchema,
  has_subgraph: z.boolean(),
  subjob_target: z.string().nullable().optional().default(null),
  connection_ref: z.string().nullable().optional().default(null),
  param_summary: z.record(ParamValueSchema).optional().default({}),
  has_code: z.boolean(),
  // Plain-English description from the generated inventory doc; "" when no
  // doc row matches this component (anti-fabrication default, never invented).
  description: z.string().optional().default(""),
});
export type JobGraphNode = z.infer<typeof JobGraphNodeSchema>;

// Edges come straight from ParsedJob.connections. line_style 0 with a
// FLOW/LOOKUP-style connector is a data-flow row; anything else is a
// trigger (control-flow) link such as COMPONENT_OK or RUN_IF.
export const JobGraphEdgeSchema = z.object({
  name: z.string(),
  source: z.string(),
  target: z.string(),
  connector_name: z.string(),
  line_style: z.number(),
});
export type JobGraphEdge = z.infer<typeof JobGraphEdgeSchema>;

export const TmapTablesSchema = z.object({
  input_tables: z.array(z.string()).optional().default([]),
  output_tables: z.array(z.string()).optional().default([]),
});
export type TmapTables = z.infer<typeof TmapTablesSchema>;

export const JobGraphResponseSchema = z.object({
  job_name: z.string(),
  group: z.string().nullable().optional().default(null),
  nodes: z.array(JobGraphNodeSchema),
  edges: z.array(JobGraphEdgeSchema),
  tmap_tables: z.record(TmapTablesSchema).optional().default({}),
});
export type JobGraphResponse = z.infer<typeof JobGraphResponseSchema>;
