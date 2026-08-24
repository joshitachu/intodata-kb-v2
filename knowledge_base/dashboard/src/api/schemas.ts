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
  status: z.enum(["pending", "running", "completed", "failed"]),
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
  run_verdict: z.enum(["draft", "failed", "published", "published_with_rag_error", "client_ready"] as const).nullable(),
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
  type: z.string(),
  size_bytes: z.number(),
  has_pdf: z.boolean(),
  modified_at: z.string(),
});
export const DocListResponseSchema = z.object({
  project_id: z.string(),
  docs: z.array(DocFileSchema),
  total: z.number(),
});

export const DocContentSchema = z.object({
  filename: z.string(),
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
  kind: z.enum(["live_evidence", "runtime_sync"]).optional().default("live_evidence"),
  build_state: z.enum(["available", "coming_soon"]).optional().default("available"),
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
});
