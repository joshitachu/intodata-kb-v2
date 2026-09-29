import { z } from "zod";
import { ApiError } from "../lib/errors";
import { LicenceStatusSchema } from "../lib/licence";

// No client-side API key — Authorization header is injected by the nginx
// reverse proxy at /mc/ so the key is never exposed in the JS bundle.
const MC_BASE = "/mc";

/**
 * A failed Mission Control request. Carries the status and the parsed
 * `detail` of the FastAPI error body so callers can branch on a code
 * (`issuing_disabled`) and show the server's reason instead of raw JSON.
 */
export class McError extends ApiError {
  readonly detail: unknown;

  constructor(status: number, body: string) {
    super(status, body);
    this.name = "McError";
    this.message = `Mission Control ${status}: ${body}`;
    let detail: unknown = body;
    try {
      const parsed: unknown = JSON.parse(body);
      detail = parsed && typeof parsed === "object" && "detail" in parsed ? (parsed as { detail: unknown }).detail : parsed;
    } catch {
      // Not JSON; keep the raw text as the detail.
    }
    this.detail = detail;
  }
}

async function mcFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${MC_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers as Record<string, string>) },
  });
  if (!res.ok) {
    throw new McError(res.status, await res.text());
  }
  return res;
}

async function mcRequest<T>(path: string, schema: z.ZodType<T>, options: RequestInit = {}): Promise<T> {
  const res = await mcFetch(path, options);
  return schema.parse(await res.json());
}

async function mcPost(path: string): Promise<{ status: string; message: string }> {
  const res = await mcFetch(path, { method: "POST" });
  return res.json();
}

/**
 * Codes whose meaning is fixed, so the sentence is ours rather than the
 * server's. Codes that carry a `message` (invalid_slug, invalid_url,
 * insecure_url) are NOT here: their message says which rule failed.
 */
const FIXED_CODE_SENTENCES: Record<string, string> = {
  issuing_disabled: "Issuing is not configured on this Mission Control (no signing key)",
  client_exists: "A client with this slug is already registered",
  managed_by_file: "This client is managed by clients.json and cannot be changed here",
};

/** A human sentence for any Mission Control or instance error `detail`. */
export function describeMcDetail(detail: unknown): string {
  if (detail == null || detail === "") return "no reason given";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI 422 validation errors: [{loc, msg, type}]
    const msgs = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : describeMcDetail(d)))
      .filter(Boolean);
    return msgs.length ? msgs.join("; ") : JSON.stringify(detail);
  }
  if (typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.code === "string" && obj.code in FIXED_CODE_SENTENCES) {
      return FIXED_CODE_SENTENCES[obj.code];
    }
    for (const key of ["message", "reason", "detail"]) {
      const value = obj[key];
      if (value != null && value !== "") return describeMcDetail(value);
    }
    if (typeof obj.code === "string") return obj.code;
    return JSON.stringify(detail);
  }
  return String(detail);
}

export function describeMcError(error: unknown): string {
  if (error instanceof McError) return `${error.status}: ${describeMcDetail(error.detail)}`;
  return error instanceof Error ? error.message : String(error);
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

/**
 * An instance's licence as reported in its heartbeat: the instance's own
 * `LicenceStatus` (same contract as `/mcp/licence/status`) plus `unknown`,
 * which the instance reports when it cannot evaluate its licence. That
 * fallback is the bare `{"state": "unknown"}`, so every field it omits must be
 * optional or defaulted here -- `enforcement` is required in the instance's
 * own contract but not in the fleet's. (Fleet schema only; the local
 * `LicenceStatusSchema` is unchanged.)
 */
export const FleetLicenceSchema = LicenceStatusSchema.extend({
  state: z.enum(["valid", "expired", "not_yet_valid", "missing", "invalid", "unknown"]),
  enforcement: z.enum(["enforce", "off"]).optional(),
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
  // Absent on instances older than the licence telemetry. A malformed block
  // degrades to null ("not reporting") instead of failing the whole fleet parse.
  licence: FleetLicenceSchema.nullable().optional().catch(null),
  _mc_source_ip: z.string().nullable().optional(),
  _mc_received_at: z.string().optional(),
});

/**
 * One slug's entry in `/ops/snapshots`. Each snapshot is client-supplied and
 * parsed on its own, so one malformed heartbeat becomes an `invalid` entry
 * for that slug instead of failing the whole fleet.
 */
export type SnapshotEntry =
  | { status: "valid"; snapshot: ClientSnapshot }
  | { status: "invalid"; error: string };

function describeZodIssues(error: z.ZodError): string {
  return error.issues
    .slice(0, 3)
    .map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`)
    .join("; ");
}

/** The outer shape must be an object keyed by slug; each value is parsed independently. */
export const AllSnapshotsSchema = z.record(z.string(), z.unknown()).transform((raw) => {
  const entries: Record<string, SnapshotEntry> = {};
  for (const [slug, value] of Object.entries(raw)) {
    const parsed = ClientSnapshotSchema.safeParse(value);
    entries[slug] = parsed.success
      ? { status: "valid", snapshot: parsed.data }
      : { status: "invalid", error: describeZodIssues(parsed.error) };
  }
  return entries;
});

export type ClientSnapshot = z.infer<typeof ClientSnapshotSchema>;
export type FleetProject = z.infer<typeof FleetProjectSchema>;
export type FleetTotals = z.infer<typeof FleetTotalsSchema>;
export type ModelStat = z.infer<typeof ModelStatSchema>;
export type RagMetrics = z.infer<typeof RagMetricsSchema>;
export type FleetLicence = z.infer<typeof FleetLicenceSchema>;

// ── Licensing (central Mission Control only) ──────────────────────────────────

export const LicencePlanSchema = z.object({
  name: z.string(),
  description: z.string().nullable().optional(),
  features: z.array(z.string()).default([]),
  limits: z.record(z.string(), z.number().nullable()).default({}),
});

export const LicencePlansResponseSchema = z.object({
  plans: z.array(LicencePlanSchema),
  issuing_enabled: z.boolean(),
});

export const IssueLicenceRequestSchema = z.object({
  plan: z.string(),
  months: z.number().nullable().optional(),
  start: z.string().nullable().optional(),
  end: z.string().nullable().optional(),
  start_after_current: z.boolean().optional(),
  push: z.boolean().optional(),
  return_token: z.boolean().optional(),
  issued_by: z.string().nullable().optional(),
});

export const LicencePushResultSchema = z.object({
  attempted: z.boolean(),
  ok: z.boolean().nullable().optional(),
  status_code: z.number().nullable().optional(),
  detail: z.unknown().optional(),
  // The instance's public licence dict after install. Kept loose: a shape
  // drift here must not throw away the response of a licence already issued.
  instance_licence: z.unknown().optional(),
  // Where the instance put the licence: "current", or "next" when it was
  // queued behind the running one. Absent from older instances and MCs.
  slot: z.enum(["current", "next"]).nullable().optional().catch(null),
  // The queued licence's public view (same shape as the heartbeat licence).
  // A malformed block degrades to null rather than failing the response.
  next_licence: FleetLicenceSchema.nullable().optional().catch(null),
});

export const IssuedLicenceResponseSchema = z.object({
  licence_id: z.string(),
  client: z.string(),
  plan: z.string(),
  period_start: z.string(),
  period_end: z.string(),
  issued_at: z.string(),
  issued_by: z.string().nullable().optional(),
  push: LicencePushResultSchema,
  token: z.string().nullable().optional(),
});

export const LicenceHistoryEntrySchema = z.object({
  licence_id: z.string(),
  client: z.string(),
  plan: z.string(),
  period_start: z.string(),
  period_end: z.string(),
  issued_at: z.string(),
  issued_by: z.string().nullable().optional(),
  push_ok: z.boolean().nullable().optional(),
  push_status_code: z.number().nullable().optional(),
});

export const LicenceHistoryResponseSchema = z.object({
  licences: z.array(LicenceHistoryEntrySchema),
});

export type LicencePlan = z.infer<typeof LicencePlanSchema>;
export type LicencePlansResponse = z.infer<typeof LicencePlansResponseSchema>;
export type IssueLicenceRequest = z.infer<typeof IssueLicenceRequestSchema>;
export type LicencePushResult = z.infer<typeof LicencePushResultSchema>;
export type IssuedLicenceResponse = z.infer<typeof IssuedLicenceResponseSchema>;
export type LicenceHistoryEntry = z.infer<typeof LicenceHistoryEntrySchema>;

// ── API calls ─────────────────────────────────────────────────────────────────

export async function fetchAllSnapshots(): Promise<Record<string, SnapshotEntry>> {
  return mcRequest("/ops/snapshots", AllSnapshotsSchema);
}

export async function triggerScan(clientSlug: string, projectId: string) {
  return mcPost(`/ops/clients/${clientSlug}/trigger/scan/${projectId}`);
}

export async function triggerGenerate(clientSlug: string, projectId: string) {
  return mcPost(`/ops/clients/${clientSlug}/trigger/generate/${projectId}`);
}

export async function fetchLicencePlans(): Promise<LicencePlansResponse> {
  return mcRequest("/ops/licensing/plans", LicencePlansResponseSchema);
}

export async function fetchClientLicences(clientSlug: string): Promise<LicenceHistoryEntry[]> {
  const data = await mcRequest(
    `/ops/clients/${encodeURIComponent(clientSlug)}/licences`,
    LicenceHistoryResponseSchema,
  );
  return data.licences;
}

/**
 * Issue (and optionally push) a licence. The response may carry the signed
 * token when `return_token` was requested; callers must keep it out of any
 * cache (see `useIssueLicence`).
 */
export async function issueClientLicence(
  clientSlug: string,
  body: IssueLicenceRequest,
): Promise<IssuedLicenceResponse> {
  return mcRequest(`/ops/clients/${encodeURIComponent(clientSlug)}/licences`, IssuedLicenceResponseSchema, {
    method: "POST",
    body: JSON.stringify(IssueLicenceRequestSchema.parse(body)),
  });
}

// ── Client registry (central Mission Control only) ────────────────────────────

export const FleetClientSchema = z.object({
  slug: z.string(),
  mcp_url: z.string().nullable(),
  has_control_key: z.boolean(),
  heartbeat_auth: z.enum(["client_key", "shared_key_only"]),
  source: z.enum(["registry", "file"]),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
  last_heartbeat_at: z.string().nullable(),
});

export const FleetClientsResponseSchema = z.object({
  clients: z.array(FleetClientSchema),
  telemetry_base_url: z.string().nullable(),
  // True when Mission Control could not read its registry file: the client
  // list may be incomplete and registry changes are refused. Absent on older
  // Mission Controls.
  registry_unavailable: z.boolean().optional().default(false),
});

/** The three env lines a client needs to heartbeat to this Mission Control. */
export const ClientEnvSchema = z.object({
  MCP_OPS_TELEMETRY_URL: z.string().nullable(),
  MCP_OPS_TELEMETRY_API_KEY: z.string(),
  MCP_OPS_CLIENT_SLUG: z.string(),
});

export const CreatedClientResponseSchema = z.object({
  client: FleetClientSchema,
  telemetry_key: z.string(),
  client_env: ClientEnvSchema,
});

export const TelemetryKeyResponseSchema = z.object({
  telemetry_key: z.string(),
  client_env: ClientEnvSchema,
});

export const UpdatedClientResponseSchema = z.object({
  client: FleetClientSchema,
});

export type FleetClient = z.infer<typeof FleetClientSchema>;
export type FleetClientsResponse = z.infer<typeof FleetClientsResponseSchema>;
export type ClientEnv = z.infer<typeof ClientEnvSchema>;
export type CreatedClientResponse = z.infer<typeof CreatedClientResponseSchema>;
export type TelemetryKeyResponse = z.infer<typeof TelemetryKeyResponseSchema>;

export interface CreateClientRequest {
  slug: string;
  mcp_url: string;
  control_key: string | null;
}

/** Only the fields present are changed; `control_key: null` clears the key. */
export interface UpdateClientRequest {
  mcp_url?: string;
  control_key?: string | null;
}

function clientPath(slug: string): string {
  return `/ops/fleet/clients/${encodeURIComponent(slug)}`;
}

export async function fetchFleetClients(): Promise<FleetClientsResponse> {
  return mcRequest("/ops/fleet/clients", FleetClientsResponseSchema);
}

/**
 * Register a client. The response carries the client's heartbeat key, shown
 * once; callers must keep it out of any cache (see `useAddFleetClient`).
 */
export async function createFleetClient(body: CreateClientRequest): Promise<CreatedClientResponse> {
  return mcRequest("/ops/fleet/clients", CreatedClientResponseSchema, {
    method: "POST",
    body: JSON.stringify({ slug: body.slug, mcp_url: body.mcp_url, control_key: body.control_key }),
  });
}

export async function updateFleetClient(slug: string, body: UpdateClientRequest): Promise<FleetClient> {
  const data = await mcRequest(clientPath(slug), UpdatedClientResponseSchema, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  return data.client;
}

/** Issue a new heartbeat key; the old one stops working. Shown once. */
export async function rotateTelemetryKey(slug: string): Promise<TelemetryKeyResponse> {
  return mcRequest(`${clientPath(slug)}/telemetry-key`, TelemetryKeyResponseSchema, { method: "POST" });
}

export async function deleteFleetClient(slug: string): Promise<void> {
  await mcFetch(clientPath(slug), { method: "DELETE" });
}
