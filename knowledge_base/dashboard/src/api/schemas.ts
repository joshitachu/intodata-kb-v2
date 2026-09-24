import { z } from "zod";

export const ProjectSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  repo_url: z.string().nullable(),
  local_path: z.string(),
  platform: z.string(),
  doc_generation_provider: z.string().nullable(),
  source_type: z.string(),
  // `scanned` is the status returned by the project registry after a
  // successful source scan. Keep the explicit union so genuinely malformed
  // project records still fail closed, while accepting every lifecycle state
  // emitted by the backend.
  status: z.enum(["registered", "scanning", "scanned", "generating", "ready", "error"]),
  last_scan_at: z.string().nullable(),
  last_generate_at: z.string().nullable(),
  last_rag_push_at: z.string().nullable(),
  job_count: z.number(),
  doc_count: z.number(),
  error_message: z.string().nullable(),
  last_ingested_commit_sha: z.string().nullable(),
  auto_reingest_enabled: z.boolean(),
  auto_reingest_last_checked_at: z.string().nullable(),
  auto_reingest_error: z.string().nullable(),
  git_update_summary: z.record(z.string(), z.unknown()).nullable(),
  has_github_token: z.boolean(),
  // Deliberately a plain string, not an enum. This is branching/display metadata,
  // and `ProjectListResponseSchema` validates an ARRAY of these, so a single
  // project carrying a kind the dashboard has not been taught yet rejects the
  // whole list and blanks EVERY project on the page -- which is what happened
  // when the backend gained "azure_devops" alongside "pat" and "app". Known
  // values are `ConnectionKind` in types.ts; an unknown one must fall through to
  // the default branch, never fail the parse.
  connection_kind: z.string().default("pat"),
  github_account_login: z.string().nullable().default(null),
  webhook_status: z.enum(["active", "not_registered", "unknown"] as const).nullable(),
  instruction_pack_dir: z.string().nullable(),
  plugin_pack_id: z.number().nullable(),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
});

export const ProjectListResponseSchema = z.object({
  projects: z.array(ProjectSchema),
  total: z.number(),
});

export const GenerateRunSchema = z.object({
  id: z.number(),
  project_id: z.string(),
  triggered_by: z.string(),
  // Composite orchestration: sub-project partition of a "composite" child run.
  partition_name: z.string().nullable().optional(),
  status: z.enum(["pending", "running", "completed", "completed_warnings", "failed", "cancelled"]),
  started_at: z.string(),
  completed_at: z.string().nullable(),
  cost_usd: z.number().nullable(),
  duration_ms: z.number().nullable(),
  duration_api_ms: z.number().nullable(),
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  num_turns: z.number().nullable(),
  session_id: z.string().nullable(),
  doc_count: z.number(),
  error_message: z.string().nullable(),
  log_file: z.string().nullable(),
  model_name: z.string().nullable(),
  write_model_name: z.string().nullable(),
  quality_score: z.number().nullable(),
  auto_verdict: z.enum(["PASS", "FAIL"]).nullable(),
  auto_baseline_run_id: z.number().nullable(),
  run_verdict: z.enum(["draft", "failed", "cancelled", "published", "published_with_rag_error", "client_ready", "held_manual_review", "held_client_readiness"] as const).nullable(),
  gate_results: z.record(z.string(), z.unknown()).nullable(),
});

export const GenerateRunListResponseSchema = z.object({
  runs: z.array(GenerateRunSchema),
  total: z.number(),
});

export const ScanRunSchema = z.object({
  id: z.number(),
  project_id: z.string(),
  triggered_by: z.string(),
  status: z.enum(["pending", "running", "completed", "failed"]),
  started_at: z.string(),
  completed_at: z.string().nullable(),
  job_count: z.number(),
  log_file: z.string().nullable(),
});

export const ScanRunListResponseSchema = z.object({
  runs: z.array(ScanRunSchema),
  total: z.number(),
});

export const ScanWarningSchema = z.object({
  kind: z.string(),
  file: z.string(),
  message: z.string(),
  phase: z.string(),
});

export const ScanStatusResponseSchema = z.object({
  project_id: z.string(),
  status: z.string(),
  last_scan_at: z.string().nullable(),
  cache_age_minutes: z.number().nullable(),
  job_count: z.number(),
  doc_count: z.number(),
  error_message: z.string().nullable(),
  previous_job_count: z.number().nullable(),
  job_delta: z.number().nullable(),
  warnings: z.array(ScanWarningSchema),
  warnings_count: z.number(),
});

export const StatusResponseSchema = z.object({
  status: z.string(),
  message: z.string(),
});

export const PlatformDescriptorSchema = z.object({
  id: z.string(),
  label: z.string(),
  unit_label: z.string(),
  unit_label_plural: z.string(),
  badge_color: z.string(),
  accent_color: z.string(),
});
export const PlatformListResponseSchema = z.object({
  platforms: z.array(PlatformDescriptorSchema),
});

export const ModelDescriptorSchema = z.object({
  id: z.string(),
  provider: z.string(),
  label: z.string(),
  supports_analysis: z.boolean(),
  supports_writing: z.boolean(),
  is_default_analysis: z.boolean(),
  is_default_investigation: z.boolean(),
  is_default_writing: z.boolean(),
  is_local: z.boolean(),
  // Newer backends include availability + rag_capable; default for older ones.
  available: z.boolean().optional().default(true),
  unavailable_reason: z.string().nullable().optional().default(null),
  rag_capable: z.boolean().optional().default(true),
});
export const ModelListResponseSchema = z.object({
  models: z.array(ModelDescriptorSchema),
});

export const InstanceSettingsResponseSchema = z.object({
  settings: z.record(z.string(), z.string()),
});
export const InstanceSettingsUpdateRequestSchema = z.object({
  settings: z.record(z.string(), z.string().nullable()),
});

export const LogFileSchema = z.object({
  filename: z.string(),
  project_id: z.string(),
  timestamp: z.string(),
  size_bytes: z.number(),
});
export const LogFileListResponseSchema = z.object({
  files: z.array(LogFileSchema),
  total: z.number(),
});

export const LogContentSchema = z.object({
  filename: z.string(),
  content: z.string(),
});

export const DocFileSchema = z.object({
  name: z.string(),
  // Sub-project this document belongs to; null for a flat project. Undeclared
  // fields are STRIPPED by zod, so leaving this out did not degrade
  // gracefully -- it made the server's answer permanently invisible to the
  // browser, and every content request for a partitioned project 404'd.
  // `name` alone does not identify a document: every partition emits the same
  // enterprise document names.
  subproject: z.string().nullable().optional().default(null),
  type: z.string(),
  size_bytes: z.number(),
  has_pdf: z.boolean(),
  modified_at: z.string(),
});
export const DocListResponseSchema = z.object({
  project_id: z.string(),
  docs: z.array(DocFileSchema),
  total: z.number(),
  // Sub-projects present in `docs`, in display order. Empty for a flat
  // project, which is what lets the viewer hide the grouping entirely rather
  // than showing a one-entry control.
  subprojects: z.array(z.string()).optional().default([]),
});

export const DocContentSchema = z.object({
  filename: z.string(),
  // Echoed back by the server: which tree the bytes actually came from.
  subproject: z.string().nullable().optional().default(null),
  content: z.string(),
});

export const ProjectUsageSchema = z.object({
  project_id: z.string(),
  total_generate_runs: z.number(),
  completed_generate_runs: z.number(),
  total_input_tokens: z.number(),
  total_output_tokens: z.number(),
  total_cost_usd: z.number(),
  total_scan_runs: z.number(),
  completed_scan_runs: z.number(),
});

const RunQualityDetailsSchema = z.object({
  overall: z.number().nullable().optional(),
}).passthrough();

export const RunComparisonRunSchema = z.object({
  id: z.number(),
  run_id: z.number(),
  status: z.string(),
  started_at: z.string().nullable(),
  completed_at: z.string().nullable(),
  doc_count: z.number(),
  provider: z.string().nullable(),
  analysis_model: z.string().nullable(),
  model_name: z.string().nullable(),
  write_model: z.string().nullable(),
  write_model_name: z.string().nullable(),
  quality: z.number().nullable(),
  quality_score: z.number().nullable(),
  quality_pass: z.boolean(),
  doc_quality: RunQualityDetailsSchema.nullable(),
  cost_usd: z.number().nullable(),
  duration_ms: z.number().nullable(),
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  num_turns: z.number().nullable(),
  metrics_found: z.boolean(),
  auto_verdict: z.enum(["PASS", "FAIL"]).nullable(),
  auto_baseline_run_id: z.number().nullable(),
});

export const RunComparisonSchema = z.object({
  project_id: z.string(),
  run_a: RunComparisonRunSchema,
  run_b: RunComparisonRunSchema,
  delta: z.record(z.string(), z.number().nullable()),
  delta_pct: z.record(z.string(), z.number().nullable()),
  quality_delta: z.number().nullable(),
  quality_delta_pct: z.number().nullable(),
  verdict: z.enum(["PASS", "FAIL"]),
  checks: z.array(z.record(z.string(), z.unknown())),
});

export const QualityHistoryEntrySchema = z.object({
  run_id: z.number().nullable(),
  started_at: z.string().nullable(),
  analysis_model: z.string().nullable(),
  write_model: z.string().nullable(),
  quality: z.number().nullable(),
  cost_usd: z.number().nullable(),
  duration_ms: z.number().nullable(),
  regression: z.boolean(),
  quality_delta_pct: z.number().nullable(),
  cost_regression: z.boolean(),
  cost_delta_pct: z.number().nullable(),
  duration_regression: z.boolean(),
  duration_delta_pct: z.number().nullable(),
});
export const QualityHistoryResponseSchema = z.object({
  project_id: z.string(),
  history: z.array(QualityHistoryEntrySchema),
  total: z.number(),
});

export const ReviewQuestionSchema = z.object({
  id: z.string(),
  question: z.string(),
  score: z.number(),
});
export const QuestionnaireResponseSchema = z.object({
  project_id: z.string(),
  run_id: z.number(),
  questions: z.array(ReviewQuestionSchema),
});

export const ReviewAggregateResponseSchema = z.object({
  project_id: z.string(),
  run_id: z.number().nullable(),
  total_reviews: z.number(),
  average_overall: z.number(),
  average_per_question: z.record(z.string(), z.number()),
  pass_result: z.boolean(),
  reviews: z.array(z.unknown()),
});

export const InstructionPackMetadataSchema = z.object({
  path: z.string(),
  relative_path: z.string(),
  version: z.string(),
  exists: z.boolean(),
  is_local: z.boolean(),
  supported: z.boolean(),
  preambles: z.array(z.object({ key: z.string(), bytes: z.number(), chars: z.number() })),
});

export const PluginPackSchema = z.object({
  id: z.number(),
  name: z.string(),
  manifest_name: z.string().nullable(),
  version: z.string(),
  description: z.string().nullable(),
  repo_url: z.string().nullable(),
  repo_branch: z.string().nullable(),
  last_commit_sha: z.string().nullable(),
  auto_update: z.boolean(),
  // The server intentionally omits the local install `path`; it returns
  // governance/certification state instead.
  origin: z.string(),
  certification_status: z.string(),
  certification_provenance: z.string().nullable().optional(),
  usable: z.boolean().default(true),
  mutable: z.boolean(),
  blocked_reason: z.string().nullable().optional(),
});
export const PluginPackListSchema = z.array(PluginPackSchema);

export const StarterPackSchema = z.object({
  repo_url: z.string(),
  clone_url: z.string(),
  zip_url: z.string(),
  name: z.string().nullable(),
  version: z.string().nullable(),
  description: z.string().nullable(),
});

export const PluginPackBranchSchema = z.object({
  branch: z.string(),
  name: z.string(),
  manifest_name: z.string(),
  version: z.string(),
  description: z.string().nullable(),
  repo_url: z.string(),
  commit_sha: z.string(),
  registered: z.boolean(),
  registered_pack_id: z.number().nullable(),
});

export const PluginPackBranchDiscoverySchema = z.object({
  repo_url: z.string(),
  branches: z.array(PluginPackBranchSchema),
});

export const WebhookStatusSchema = z.object({
  project_id: z.string(),
  webhook_status: z.string(),
  webhook_id: z.number().nullable(),
});

export const AssignPluginPackResponseSchema = z.object({
  project_id: z.string(),
  plugin_pack_id: z.number().nullable(),
});

export const PluginPackFileSchema = z.object({
  path: z.string(),
  content: z.string(),
});
export const PluginPackFilesSchema = z.array(z.string());

export const GenerateAssessmentResponseSchema = z.object({
  project_id: z.string(),
  status: z.string(),
  html_path: z.string().nullable(),
  error: z.string().nullable(),
});

export const SubmitReviewResponseSchema = z.object({
  status: z.string(),
  path: z.string(),
});

// Connector contracts deliberately contain safe metadata only. In particular,
// none of these schemas model credential fields returned by the server.
export const ConnectorCapabilitySchema = z.object({
  id: z.string(),
  label: z.string().optional().default(""),
  description: z.string().nullable().optional().default(null),
  read_only: z.boolean().optional().default(true),
  gather_eligible: z.boolean().optional().default(true),
});

export const ConnectorProviderSchema = z.object({
  provider_id: z.string(),
  label: z.string(),
  version: z.string().nullable().optional().default(null),
  authentication_types: z.array(z.string()).optional().default([]),
  capabilities: z.array(ConnectorCapabilitySchema).optional().default([]),
  enabled: z.boolean().optional().default(true),
  // Directory presentation, served from the provider manifest so adding a
  // connector needs no dashboard change. Defaults keep older servers working.
  // Plain strings, not enums, for the same reason as `managed_by` below: this
  // schema is validated as an ARRAY, so ONE provider carrying a kind or build
  // state this build has not been taught yet would reject the whole catalogue
  // and blank every connector row -- in the directory AND in the Generate
  // dialog's live-data panel. An unfamiliar value must render as itself and be
  // treated as unknown (see `lib/connectors.ts`), never take the page down.
  kind: z.string().optional().default("live_evidence"),
  build_state: z.string().optional().default("available"),
  // WHERE this provider's credential is stored: "connector_store" (the MCP
  // server's own encrypted store) or "runtime_sources" (the RAG backend's
  // tables, next to the scheduler that polls with it). It says nothing about
  // whether the provider can be connected from this page -- the dialog registry
  // decides that, and each dialog knows which lane to post to.
  //
  // Deliberately a plain string, not an enum, for the reason documented on
  // `connection_kind` above: this schema is validated as an ARRAY, so one
  // provider carrying a `managed_by` this build has not been taught yet would
  // reject the whole catalogue and blank every connector row. An unknown value
  // renders as an unfamiliar storage location and is otherwise inert.
  managed_by: z.string().optional().default("connector_store"),
  description: z.string().optional().default(""),
});

export const ConnectorCatalogResponseSchema = z.object({
  providers: z.array(ConnectorProviderSchema),
});

export const ConnectorStatusSchema = z.enum([
  "connected",
  "degraded",
  "not_connected",
  "disabled",
  "unknown",
]);

export const ConnectorInstanceSchema = z.object({
  id: z.string(),
  provider_id: z.string(),
  display_name: z.string(),
  ownership_scope: z.string().optional().default("workspace"),
  enabled: z.boolean().optional().default(true),
  status: ConnectorStatusSchema.optional().default("unknown"),
  last_checked_at: z.string().nullable().optional().default(null),
  health_code: z.string().nullable().optional().default(null),
  provider_version: z.string().nullable().optional().default(null),
  redaction_policy_version: z.string().nullable().optional().default(null),
  has_credential: z.boolean().optional().default(false),
  has_kibana_credential: z.boolean().optional().default(false),
  created_at: z.string().nullable().optional().default(null),
  updated_at: z.string().nullable().optional().default(null),
});

export const ConnectorListResponseSchema = z.object({
  connectors: z.array(ConnectorInstanceSchema),
  total: z.number().optional(),
});

export const ConnectorSourceSchema = z.object({
  id: z.string(),
  connector_instance_id: z.string(),
  source_type: z.string(),
  display_label: z.string(),
  enabled: z.boolean().optional().default(true),
  field_profile: z.record(z.string(), z.unknown()).optional().default({}),
  discovered_at: z.string().nullable().optional().default(null),
});

export const ConnectorSourceListResponseSchema = z.object({
  connector_instance_id: z.string(),
  sources: z.array(ConnectorSourceSchema),
  total: z.number().optional(),
});

export const KibanaSpaceListResponseSchema = z.object({
  spaces: z.array(z.object({ id: z.string(), name: z.string() })),
});

export const KibanaDataViewListResponseSchema = z.object({
  data_views: z.array(z.object({ id: z.string(), title: z.string() })),
});

export const ProjectAvailableConnectorSchema = z.object({
  connector: ConnectorInstanceSchema,
  sources: z.array(ConnectorSourceSchema),
});

export const ProjectAvailableConnectorListResponseSchema = z.object({
  connectors: z.array(ProjectAvailableConnectorSchema),
});

export const ProjectConnectorBindingSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  connector_instance_id: z.string(),
  connector_display_name: z.string().nullable().optional().default(null),
  connector_provider_id: z.string().nullable().optional().default(null),
  source_ids: z.array(z.string()).optional().default([]),
  capability_ids: z.array(z.string()).optional().default([]),
  budgets: z.record(z.string(), z.number()).optional().default({}),
  max_time_window_days: z.number().nullable().optional().default(null),
  enabled: z.boolean().optional().default(true),
  version: z.number().optional().default(1),
});

export const ProjectConnectorBindingListResponseSchema = z.object({
  project_id: z.string(),
  bindings: z.array(ProjectConnectorBindingSchema),
});

// --- Runtime sources (proxied by the MCP server to the RAG backend) ---
//
// A runtime source is a credential the RAG backend's scheduler polls. The MCP
// server proxies these on the caller's own session, so the dashboard can offer
// a connect form without the secret ever passing through the connector store.
// As with the connector schemas above, nothing here models a credential value:
// they are write-only and no server returns them.

export const RuntimeSourceCredentialFieldSchema = z.object({
  name: z.string(),
  label: z.string().optional().default(""),
  // secret | url | text. A plain string, not an enum: this is validated as an
  // ARRAY, so one unfamiliar field type from a newer backend would otherwise
  // reject the whole schema and leave the connect form with no inputs at all.
  // An unknown type must degrade to a text box.
  type: z.string().optional().default("text"),
  required: z.boolean().optional().default(true),
});

/**
 * A provider's selectable ingest scope, e.g. Talend environments.
 *
 * Its presence is what tells the connect dialog to show the test-then-choose
 * step, so a provider that ingests everything simply omits it and the dialog
 * needs no per-connector branch.
 */
export const RuntimeScopeSelectionSchema = z.object({
  field: z.string(),
  label: z.string().optional().default("Scope"),
  description: z.string().nullish(),
  required: z.boolean().optional().default(true),
  multiple: z.boolean().optional().default(true),
});

export const RuntimeScopeOptionSchema = z.object({
  id: z.string(),
  label: z.string().optional().default(""),
  detail: z.string().nullish(),
  default_selected: z.boolean().optional().default(false),
});

export const RuntimeSourceTypeSchema = z.object({
  provider_key: z.string(),
  provider_label: z.string().optional().default(""),
  credential_schema: z.array(RuntimeSourceCredentialFieldSchema).optional().default([]),
  scope_selection: RuntimeScopeSelectionSchema.nullish(),
});

export const RuntimeSourceTypeListResponseSchema = z.object({
  source_types: z.array(RuntimeSourceTypeSchema).optional().default([]),
});

export const RuntimeSourceScopesResponseSchema = z.object({
  scopes: z.array(RuntimeScopeOptionSchema).optional().default([]),
});

/** The slice of a project's runtime mirror that documentation runs read.
 *
 * `environment_ids: null` means every mirrored environment -- a distinct state
 * from a selection that lists them all, because a later ingest-scope change
 * widens the former and not the latter. */
export const RuntimeDocReadScopeSchema = z.object({
  environment_ids: z.array(z.string()).nullable().optional().default(null),
  environment_names: z.array(z.string()).optional().default([]),
  window_days: z.number().nullable().optional().default(null),
  configured: z.boolean().optional().default(false),
  unavailable_environment_ids: z.array(z.string()).optional().default([]),
});

/**
 * A registered TENANT, plus the projects attached to it.
 *
 * There is no owning project. `estate_key` is an internal row stamp -- the
 * value written into every mirrored row -- and it must never be rendered as a
 * project, however much it looks like one. `linked_project_ids` is who may
 * actually reach this source, and `is_ownerless` is the answer to "is anything
 * attached"; deriving that by inspecting `estate_key` is what the two separate
 * fields exist to prevent.
 *
 * Zod strips what it does not declare, so a field missing here is permanently
 * `undefined` in the browser with nothing logged. That is why every contract
 * field is listed even where the component does not read it yet.
 */
export const RuntimeSourceSchema = z.object({
  id: z.number(),
  estate_key: z.string().optional().default(""),
  linked_project_ids: z.array(z.string()).optional().default([]),
  // Defaulted rather than derived here: the MCP proxy fills it from the
  // attachment list when the backend does not state it, so there is exactly
  // one definition of "nothing is attached" in the stack.
  is_ownerless: z.boolean().optional().default(false),
  source_type: z.string(),
  label: z.string().optional().default(""),
  // Same reasoning as `managed_by`: a status this build has not been taught
  // must render as itself, not blank the row.
  status: z.string().optional().default("unknown"),
  enabled: z.boolean().optional().default(true),
  sync_interval_seconds: z.number().optional().default(60),
  disabled_reason: z.string().nullable().optional().default(null),
  last_synced_at: z.string().nullable().optional().default(null),
});

export const RuntimeSourceListResponseSchema = z.object({
  sources: z.array(RuntimeSourceSchema).optional().default([]),
  total: z.number().optional().default(0),
});

/** One project ATTACHED to a source. `source_estate_key` is a row stamp, never
 *  a project, and is not rendered anywhere. */
export const RuntimeSourceReadGrantSchema = z.object({
  id: z.number().optional().default(0),
  source_id: z.number().optional().default(0),
  project_id: z.string().optional().default(""),
  source_estate_key: z.string().optional().default(""),
  source_label: z.string().optional().default(""),
  // null means every mirrored environment; [] is a deliberate "nothing". The
  // two are NOT collapsed: a later ingest-scope widening extends the former.
  environment_ids: z.array(z.string()).nullable().optional().default(null),
  window_days: z.number().nullable().optional().default(null),
  exposes_project_ids: z.array(z.string()).optional().default([]),
  created_at: z.string().nullable().optional().default(null),
  created_by: z.string().nullable().optional().default(null),
});

export const RuntimeSourceReadGrantListResponseSchema = z.object({
  grants: z.array(RuntimeSourceReadGrantSchema).optional().default([]),
  total: z.number().optional().default(0),
});

export const RuntimeSourceReadGrantResponseSchema = z.object({
  grant: RuntimeSourceReadGrantSchema,
});

export const RuntimeSourceSyncResponseSchema = z.object({
  source: RuntimeSourceSchema.nullable().optional().default(null),
  status: z.string().optional().default("unknown"),
  records_ingested: z.number().optional().default(0),
});

/**
 * The machine-readable half of a refused registration (HTTP 409).
 *
 * A vendor tenant may be registered exactly once -- a second registration is a
 * second poll and two mirrors of one estate free to drift apart -- and the way
 * a project reaches the tenant that is already here is to ATTACH to it. That
 * refusal used to arrive as prose alone, which no client could act on: offering
 * the attachment means knowing WHICH source collided, and recovering that from
 * an English sentence is not a contract.
 *
 * `physical_identity` is now the only kind. The old `project_source_type`
 * collision -- "this project already holds a TMC source" -- was deleted with the
 * one-source-per-project rule it enforced; a project may hold several tenants.
 *
 * `kind` stays a closed enum ON PURPOSE, against the usual rule in this file. A
 * value this build cannot interpret must FAIL THE PARSE and leave the operator
 * with the server's own sentence, rather than defaulting into an affordance
 * that is wrong for their case. Nothing on the page depends on the parse
 * succeeding, so prose is always the safe floor.
 *
 * Every identifying field is nullable because the server OMITS what the caller
 * is not entitled to see: a 409 is reachable by anyone holding a working token
 * for the tenant, so an unconditional owner field would make this error a
 * project-existence oracle. Absent means "not yours to know", not "unknown".
 */
export const RuntimeSourceConflictSchema = z.object({
  kind: z.enum(["physical_identity"]),
  /** Whether THIS caller could attach a project. A button that 403s is worse than none. */
  can_request_share: z.boolean().optional().default(false),
  source_id: z.number().nullable().optional().default(null),
  source_label: z.string().nullable().optional().default(null),
  owner_project_id: z.string().nullable().optional().default(null),
});

/** The 409 body itself. `detail` stays a plain sentence for clients that render only it. */
export const RuntimeSourceConflictBodySchema = z.object({
  detail: z.string().optional().default(""),
  conflict: RuntimeSourceConflictSchema,
});

/** Inferred rather than hand-written in `types.ts`: this shape exists only on an
 *  error path, and the two must not be free to drift. */
export type RuntimeSourceConflict = z.infer<typeof RuntimeSourceConflictSchema>;

// --- Sub-projects: incremental generation ------------------------------------
//
// A project that has already been generated can gain a sub-project, and the next
// run should pay model cost only for what is new or changed. These two contracts
// are what make that visible before the operator commits.
//
// Every field the endpoints document is declared here deliberately. An
// undeclared field is not a parse error in zod -- it is silently STRIPPED, so
// the browser reads `undefined` forever with no error, no warning, and nothing
// to debug from.

/** Why every sub-project must regenerate, when something forces that. */
export const GenerationPlanGlobalInvalidationSchema = z.object({
  // Plain strings, never enums. This block exists to EXPLAIN a full-cost run;
  // a reason code this build has not been taught yet must still render its
  // detail text rather than reject the plan and leave the operator with no
  // explanation for the bill.
  reason: z.string(),
  detail: z.string(),
});

export const GenerationPlanPartitionSchema = z.object({
  name: z.string(),
  subdir: z.string(),
  // `action` is "generate" | "reuse" and `reason` is one of seven documented
  // codes. Both are plain strings for the reason documented on
  // `connection_kind` above: this object is validated as an ARRAY, so ONE
  // sub-project carrying an action or reason this build has not been taught
  // would reject the whole plan and hide the generate/reuse split for every
  // sub-project. An unfamiliar action is counted as "generate" by the renderer
  // -- over-stating cost is recoverable, under-stating it is not.
  action: z.string(),
  reason: z.string(),
  unit_count: z.number().optional().default(0),
  doc_count: z.number().optional().default(0),
  last_generated_at: z.string().nullable().optional().default(null),
});

/** Read-only dry run of the next generation: what regenerates, what is reused. */
export const GenerationPlanResponseSchema = z.object({
  project_id: z.string(),
  // Both gates for showing the preview at all. A single-run project has no
  // sub-projects to compare, and a project with no scan has nothing to compare
  // against; in either case the dialog keeps its previous behaviour.
  scan_available: z.boolean(),
  composite: z.boolean(),
  partition_count: z.number().optional().default(0),
  // The counts, not the list lengths, drive the headline and the button label:
  // the server owns the skip decision and its arithmetic is the honest one.
  will_generate: z.number().optional().default(0),
  will_reuse: z.number().optional().default(0),
  global_invalidation: GenerationPlanGlobalInvalidationSchema.nullable()
    .optional()
    .default(null),
  partitions: z.array(GenerationPlanPartitionSchema).optional().default([]),
  notes: z.array(z.string()).optional().default([]),
});

/** Result of adding a ZIP source to an existing ZIP project. */
export const AddSourceResponseSchema = z.object({
  project_id: z.string(),
  subproject: z.string(),
  // The server may have had to move the project's original flat source tree
  // into a named sub-project directory first. The operator is told, because it
  // changes where that tree's documentation will be published.
  migrated_existing_source: z.boolean().optional().default(false),
  requires_rescan: z.boolean().optional().default(true),
  status: z.string().optional().default("added"),
});

export const ConnectorTestResultSchema = z.object({
  status: z.string(),
  message: z.string().nullable().optional().default(null),
  checked_at: z.string().nullable().optional().default(null),
  details: z.record(z.string(), z.unknown()).optional().default({}),
});

export const ConnectorEvidenceFindingSchema = z.object({
  evidence_id: z.string(),
  classification: z.string(),
  summary: z.string().nullable().optional().default(null),
});

// Coverage of the mirrored runtime lane. Every field defaults, and `mode` is a
// plain string, so a run predating the coverage artifact -- or one written by a
// newer producer -- still renders instead of collapsing the popover.
export const RunRuntimeEvidenceSummarySchema = z.object({
  available: z.boolean().optional().default(false),
  mode: z.string().optional().default("unavailable"),
  window_days: z.number().nullable().optional().default(null),
  units_requested: z.number().optional().default(0),
  units_matched: z.number().optional().default(0),
  orphan_runtime_job_count: z.number().optional().default(0),
  partial: z.boolean().optional().default(false),
  // Defaults to null, NOT false: the backend always sends a boolean, so null
  // means "this payload predates the field" and the client may fall back to
  // inferring the signal. A payload that DOES report `false` must silence the
  // fallback -- an explicit denial outranks a guess.
  scope_mismatch: z.boolean().nullable().optional().default(null),
  batches: z.number().nullable().optional().default(null),
  source_key: z.string().nullable().optional().default(null),
  // Composite (partitioned) uploads. `subproject` names the sub-project a
  // figure describes; `subproject_count` is how many an aggregate spans (0 on a
  // single artifact, so "one zip" stays distinguishable from "not composite").
  // Plain strings, never an enum: sub-project names are project data.
  subproject: z.string().nullable().optional().default(null),
  subproject_count: z.number().optional().default(0),
});

export const ConnectorEvidenceSummarySchema = z.object({
  run_id: z.number(),
  available: z.boolean().optional().default(false),
  connector_count: z.number().optional().default(0),
  successful_calls: z.number().optional().default(0),
  failed_calls: z.number().optional().default(0),
  denied_calls: z.number().optional().default(0),
  truncated_calls: z.number().optional().default(0),
  observation_from: z.string().nullable().optional().default(null),
  observation_to: z.string().nullable().optional().default(null),
  limitations: z.array(z.string()).optional().default([]),
  findings: z.array(ConnectorEvidenceFindingSchema).optional().default([]),
  runtime: RunRuntimeEvidenceSummarySchema.optional().default({
    available: false,
    mode: "unavailable",
    window_days: null,
    units_requested: 0,
    units_matched: 0,
    orphan_runtime_job_count: 0,
    partial: false,
    scope_mismatch: null,
    batches: null,
    source_key: null,
    subproject: null,
    subproject_count: 0,
  }),
  // The per-sub-project rows behind an aggregated `runtime`. Empty for a
  // non-composite run and for a composite child run, which reports its own
  // sub-project in `runtime` directly.
  runtime_subprojects: z.array(RunRuntimeEvidenceSummarySchema).optional().default([]),
});
