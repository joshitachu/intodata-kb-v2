import { z } from "zod";

const MC_API_KEY = import.meta.env.VITE_MC_API_KEY ?? "mc-dev-local-secret";
const MC_BASE = "/mc";

function mcHeaders(): Record<string, string> {
  return { Authorization: `Bearer ${MC_API_KEY}`, "Content-Type": "application/json" };
}

async function mcRequest<T>(path: string, schema: z.ZodType<T>, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${MC_BASE}${path}`, { ...options, headers: mcHeaders() });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Mission Control ${res.status}: ${detail}`);
  }
  const data = await res.json();
  return schema.parse(data);
}

async function mcPost(path: string): Promise<{ status: string; message: string }> {
  const res = await fetch(`${MC_BASE}${path}`, { method: "POST", headers: mcHeaders() });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Mission Control ${res.status}: ${detail}`);
  }
  return res.json();
}

// ── Schemas ───────────────────────────────────────────────────────────────────

export const FleetProjectSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  platform: z.string(),
  source_type: z.string(),
  status: z.string(),
  last_scan_at: z.string().nullable(),
  last_generate_at: z.string().nullable(),
  job_count: z.number().nullable(),
  doc_count: z.number().nullable(),
  webhook_status: z.string().nullable(),
  error_summary: z.string().nullable(),
});

export const FleetTotalsSchema = z.object({
  project_count: z.number(),
  job_count: z.number(),
  doc_count: z.number(),
  generate_run_count: z.number(),
  scan_run_count: z.number(),
  input_tokens: z.number(),
  output_tokens: z.number(),
  cost_usd: z.number(),
  latest_run_average_quality: z.number().nullable(),
});

export const FleetGenerateRunSchema = z.object({
  id: z.number(),
  project_id: z.string(),
  status: z.string(),
  started_at: z.string().nullable(),
  cost_usd: z.number().nullable(),
  quality_score: z.number().nullable(),
  error_summary: z.string().nullable(),
});

export const ModelStatSchema = z.object({
  model: z.string(),
  run_count: z.number(),
  avg_quality: z.number().nullable(),
  avg_cost_usd: z.number().nullable(),
  avg_duration_ms: z.number().nullable(),
});

export const RagMetricsSchema = z.object({
  user_count: z.number(),
  active_users_7d: z.number(),
  active_users_30d: z.number(),
  queries_today: z.number(),
  total_queries: z.number(),
  queries_7d: z.array(z.object({ date: z.string(), count: z.number() })),
  queries_24h: z.array(z.object({ hour: z.number(), count: z.number() })),
  peak_hour: z.number().nullable(),
  conversation_count: z.number(),
  avg_queries_per_conversation: z.number(),
});

export const ClientSnapshotSchema = z.object({
  schema_version: z.string(),
  client_slug: z.string(),
  environment: z.string(),
  release_version: z.string().optional(),
  mcp_url: z.string().nullable().optional(),
  sent_at: z.string(),
  health: z.record(z.string(), z.string()),
  totals: FleetTotalsSchema,
  projects: z.array(FleetProjectSchema),
  latest_generate_runs: z.array(FleetGenerateRunSchema),
  model_stats: z.array(ModelStatSchema).optional(),
  rag: RagMetricsSchema.optional(),
  _mc_source_ip: z.string().nullable().optional(),
  _mc_received_at: z.string().optional(),
});

export const AllSnapshotsSchema = z.record(z.string(), ClientSnapshotSchema);

export type ClientSnapshot = z.infer<typeof ClientSnapshotSchema>;
export type FleetProject = z.infer<typeof FleetProjectSchema>;
export type FleetTotals = z.infer<typeof FleetTotalsSchema>;
export type ModelStat = z.infer<typeof ModelStatSchema>;
export type RagMetrics = z.infer<typeof RagMetricsSchema>;

// ── API calls ─────────────────────────────────────────────────────────────────

export async function fetchAllSnapshots(): Promise<Record<string, ClientSnapshot>> {
  return mcRequest("/ops/snapshots", AllSnapshotsSchema);
}

export async function triggerScan(clientSlug: string, projectId: string) {
  return mcPost(`/ops/clients/${clientSlug}/trigger/scan/${projectId}`);
}

export async function triggerGenerate(clientSlug: string, projectId: string) {
  return mcPost(`/ops/clients/${clientSlug}/trigger/generate/${projectId}`);
}
