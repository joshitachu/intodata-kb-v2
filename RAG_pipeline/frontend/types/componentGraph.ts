import { z } from "zod";

// Level-3 topology drill-down contract (S2), verified against the actual
// backend implementation (knowledge_base/mcp-server/src/mcp_server/schemas.py
// ::ComponentGraphResponse and services/component_graph_service.py) rather
// than the architecture doc's prose alone - tMap only today, per D4.
//
// GET /mcp/projects/{id}/jobs/{job_name}/components/{unique_name}/graph
// Non-tMap components - and any tMap the backend can't positionally match
// to its tmap_data entry - return 404 with a FastAPI
// `{ detail: { fallback: "inspector" } }` body (unwrapped/flattened by the
// Next proxy route to `{ fallback: "inspector" }`).
//
// Two real-data constraints that shape this contract (see
// component_graph_service.py's module docstring):
// - Input-table columns are NOT captured by the scanner - `columns` is
//   always [] for input tables until the S4 lineage tokenizer backfills them.
// - Edges name only their TARGET (table, column) and the raw expression.
//   There is no resolved SOURCE column/table pre-S4 - `resolved_refs` stays
//   empty and `confidence` stays "opaque" until the deterministic tokenizer
//   (S4) lands. The frontend must not invent a source column; see
//   lib/componentGraphTransform.ts for how this renders honestly (table-level
//   fan-out to every structurally possible source, never a specific guess).

export const ComponentGraphTableSchema = z.object({
  table_name: z.string(),
  columns: z.array(z.string()).optional().default([]),
  // Upstream component feeding this row, resolved server-side via the job's
  // connections array (input tables only; null for Var/output tables).
  source_component: z.string().nullable().optional().default(null),
});
export type ComponentGraphTable = z.infer<typeof ComponentGraphTableSchema>;

export const ComponentGraphEdgeSchema = z.object({
  target_table: z.string(),
  target_column: z.string(),
  expression: z.string(),
  resolved_refs: z.array(z.string()).optional().default([]),
  // Widens to "certain" | "likely" | "dynamic" | "opaque" once the S4
  // tokenizer lands (architecture doc, section 2.2, D2). The backend field
  // is a plain string today (always "opaque" pre-S4), so this stays a bare
  // string rather than a strict enum that could reject a legitimate future
  // value the frontend doesn't know about yet.
  confidence: z.string().optional().default("opaque"),
});
export type ComponentGraphEdge = z.infer<typeof ComponentGraphEdgeSchema>;

// Confidence values that mean "resolved to an exact source" once S4 lands.
export const RESOLVED_CONFIDENCE_VALUES: readonly string[] = ["certain", "likely"];

export const ComponentGraphResponseSchema = z.object({
  project_id: z.string(),
  job_name: z.string(),
  component_unique_name: z.string(),
  input_tables: z.array(ComponentGraphTableSchema).optional().default([]),
  // Always present server-side (defaults to an empty "Var" table when this
  // tMap has no var_entries) - never null.
  var_table: ComponentGraphTableSchema,
  output_tables: z.array(ComponentGraphTableSchema).optional().default([]),
  edges: z.array(ComponentGraphEdgeSchema).optional().default([]),
});
export type ComponentGraphResponse = z.infer<typeof ComponentGraphResponseSchema>;

// 404 fallback contract: any component without a level-3 graph.
export const ComponentGraphFallbackSchema = z.object({
  fallback: z.literal("inspector"),
});
export type ComponentGraphFallback = z.infer<typeof ComponentGraphFallbackSchema>;
