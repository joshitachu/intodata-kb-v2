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
  connection_kind: z.enum(["pat", "app"] as const).default("pat"),
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

const NexusPackCatalogItemSchema = z.object({
  id: z.number(),
  name: z.string(),
  version: z.string(),
  description: z.string().nullable().optional(),
});

const NexusPersonalPackSchema = NexusPackCatalogItemSchema.extend({
  output_documents: z.array(z.string()),
});

export const NexusPackWorkspaceSchema = z.object({
  project_id: z.string(),
  organization_packs: z.array(NexusPackCatalogItemSchema),
  personal_packs: z.array(NexusPersonalPackSchema),
  selected_organization_pack_id: z.number().nullable(),
  selected_private_version_ids: z.array(z.number()),
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

// Additive, feature-flagged agentic experiment contracts.  These responses are
// deliberately limited to sanitized identities, task telemetry, and metrics;
// they never contain prompts, source bytes, or candidate document content.
export const AgenticExperimentArtifactSchema = z.object({
  branch: z.string(),
  kind: z.string(),
  logical_name: z.string(),
  relative_path: z.string(),
  sha256: z.string(),
  media_type: z.string(),
  size_bytes: z.number(),
});

export const AgenticExperimentBranchMetricsSchema = z.object({
  document_count: z.number(),
  duration_ms: z.number().nullable(),
  provider_latency_ms: z.number().nullable(),
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  billed_cost_usd: z.string().nullable(),
  quality_score: z.number().nullable(),
  model_id: z.string().nullable(),
  attempt_count: z.number().nullable(),
  retry_count: z.number().nullable(),
});

export const AgenticExperimentSchema = z.object({
  experiment_id: z.string(),
  project_id: z.string(),
  baseline_run_id: z.number().nullable(),
  status: z.string(),
  comparison_status: z.string(),
  candidate_verdict: z.string(),
  runtime_id: z.string(),
  provider_id: z.string().nullable(),
  model_id: z.string().nullable(),
  source_snapshot_id: z.string().nullable(),
  source_snapshot_frozen: z.boolean(),
  recipe_snapshot_id: z.string().nullable(),
  recipe_id: z.string().nullable(),
  recipe_version: z.string().nullable(),
  baseline_manifest_sha256: z.string().nullable(),
  baseline_document_count: z.number(),
  artifact_count: z.number(),
  publishes: z.literal(false),
  candidate_not_started: z.boolean(),
  error_message: z.string().nullable(),
  baseline_metrics: AgenticExperimentBranchMetricsSchema,
  candidate_metrics: AgenticExperimentBranchMetricsSchema,
  safety_warnings: z.array(z.object({
    path: z.string(),
    category: z.string(),
    severity: z.string(),
  })),
  grounded_progress: z.object({
    stage: z.string(),
    accepted_artifact_count: z.number(),
    required_artifact_count: z.number(),
    roles: z.array(z.object({
      role_id: z.string(),
      stage: z.string(),
      status: z.enum(["pending", "completed"]),
      accepted_artifact_count: z.number(),
      owned_artifact_count: z.number(),
    })),
  }).nullable().optional().default(null),
  artifacts: z.array(AgenticExperimentArtifactSchema).default([]),
});

export const AgenticExperimentListSchema = z.array(AgenticExperimentSchema);

export const AgenticExperimentCapabilitiesSchema = z.object({
  feature_enabled: z.literal(true),
  provider_neutral: z.literal(true),
  model_selection: z.literal("runtime_discovered"),
  publishes: z.literal(false),
  candidate_dispatch_available: z.boolean(),
  paid_execution_enabled: z.boolean(),
  opencode_version: z.string(),
  grounded_runtimes: z.array(z.object({
    runtime_id: z.enum(["opencode", "openai-agents"]),
    available: z.boolean(),
    routes: z.array(z.object({
      provider_id: z.string(),
      model_id: z.string(),
      display_name: z.string(),
    })).default([]),
    provider_id: z.string().optional(),
    model_ids: z.array(z.string()).optional(),
    paid_execution_confirmation_required: z.boolean(),
    blockers: z.array(z.string()),
  })),
  supported_source_kinds: z.array(z.string()),
  recipes: z.array(z.object({
    recipe_id: z.string(),
    recipe_version: z.string(),
    source_requirements: z.array(z.object({
      source_role: z.string(),
      required_capabilities: z.array(z.string()),
      optional: z.boolean(),
    })),
    required_model_capabilities: z.array(z.string()),
    output_ids: z.array(z.string()),
    document_output_count: z.number(),
  })),
  execution_blockers: z.array(z.string()),
});

export const AgenticSourceSnapshotSchema = z.object({
  experiment_id: z.string(),
  snapshot_id: z.string(),
  manifest_sha256: z.string(),
  security_state: z.literal("approved"),
  manifest_relative_path: z.string(),
  item_count: z.number(),
  source_kinds: z.array(z.string()),
  source_roles: z.array(z.string()),
  publishes: z.literal(false),
});

export const AgenticRecipeSnapshotSchema = z.object({
  experiment_id: z.string(),
  snapshot_id: z.string(),
  recipe_id: z.string(),
  recipe_version: z.string(),
  graph_sha256: z.string(),
  effective_scope_sha256: z.string(),
  manifest_relative_path: z.string(),
  task_count: z.number(),
  document_output_count: z.number(),
  preflight_required: z.literal(true),
  publishes: z.literal(false),
});

export const AgenticTaskAttemptSchema = z.object({
  attempt_id: z.string(),
  attempt_number: z.number(),
  status: z.string(),
  started_at: z.string(),
  completed_at: z.string().nullable(),
  wall_duration_ms: z.number().nullable(),
  provider_latency_ms: z.number().nullable(),
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  cache_read_tokens: z.number().nullable(),
  provider_cost_usd: z.string().nullable(),
});

export const AgenticTaskEventSchema = z.object({
  event_id: z.number(),
  attempt_id: z.string().nullable(),
  sequence_number: z.number(),
  event_type: z.string(),
  occurred_at: z.string(),
});

export const AgenticExperimentTaskSchema = z.object({
  task_run_id: z.string(),
  node_id: z.string(),
  task_type: z.string(),
  role: z.string(),
  dependencies: z.array(z.string()),
  output_ids: z.array(z.string()),
  status: z.string(),
  attempt: z.number(),
  max_attempts: z.number(),
  runtime_id: z.string().nullable(),
  provider_id: z.string().nullable(),
  model_id: z.string().nullable(),
  started_at: z.string().nullable(),
  completed_at: z.string().nullable(),
  attempts: z.array(AgenticTaskAttemptSchema),
  events: z.array(AgenticTaskEventSchema),
  publishes: z.literal(false),
});

export const AgenticExperimentTaskListSchema = z.object({
  experiment_id: z.string(),
  tasks: z.array(AgenticExperimentTaskSchema),
  total: z.number(),
  publishes: z.literal(false),
});

export const AgenticReconcileResultSchema = z.object({
  experiment_id: z.string(),
  persisted_status: z.string(),
  derived_status: z.string(),
  recovered_expired_task_ids: z.array(z.string()),
  manual_retry_task_ids: z.array(z.string()),
  missing_required_outputs: z.array(z.string()),
  duplicate_required_outputs: z.array(z.string()),
  tasks: z.array(z.object({
    task_run_id: z.string(),
    state: z.string(),
    manual_retry_required: z.boolean(),
    reason: z.string().nullable(),
  })),
  dispatch_performed: z.literal(false),
  publishes: z.literal(false),
});

export const AgenticRetentionResultSchema = z.object({
  experiment_id: z.string(),
  project_id: z.string(),
  state: z.enum(["quarantined", "restored"]),
  receipt_sha256: z.string(),
  source_relative_path: z.string(),
  trash_relative_path: z.string(),
  hard_deleted: z.literal(false),
  publishes: z.literal(false),
});
