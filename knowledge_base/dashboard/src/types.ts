export type DocSource = "published" | "staging";

export interface Project {
  id: string;
  display_name: string;
  repo_url: string | null;
  local_path: string;
  platform: string;
  doc_generation_provider: string | null;
  source_type: string;
  status: string;
  last_scan_at: string | null;
  last_generate_at: string | null;
  last_rag_push_at: string | null;
  job_count: number;
  doc_count: number;
  error_message: string | null;
  last_ingested_commit_sha: string | null;
  auto_reingest_enabled: boolean;
  auto_reingest_last_checked_at: string | null;
  auto_reingest_error: string | null;
  git_update_summary: Record<string, unknown> | null;
  has_github_token: boolean;
  connection_kind: "pat" | "app";
  github_account_login: string | null;
  webhook_status: "active" | "not_registered" | "unknown" | null;
  instruction_pack_dir: string | null;
  plugin_pack_id: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PluginPack {
  id: number;
  name: string;
  manifest_name: string | null;
  version: string;
  description: string | null;
  repo_url: string | null;
  repo_branch: string | null;
  last_commit_sha: string | null;
  auto_update: boolean;
  origin: string;
  certification_status: string;
  certification_provenance?: string | null;
  mutable: boolean;
  blocked_reason?: string | null;
}

export interface StarterPack {
  repo_url: string;
  clone_url: string;
  zip_url: string;
  name: string | null;
  version: string | null;
  description: string | null;
}

export interface PluginPackBranch {
  branch: string;
  name: string;
  manifest_name: string;
  version: string;
  description: string | null;
  repo_url: string;
  commit_sha: string;
  registered: boolean;
  registered_pack_id: number | null;
}

export interface PluginPackBranchDiscovery {
  repo_url: string;
  branches: PluginPackBranch[];
}

export interface NexusPackCatalogItem {
  id: number;
  name: string;
  version: string;
  description?: string | null;
}

export interface NexusPersonalPack extends NexusPackCatalogItem {
  output_documents: string[];
}

export interface NexusPackWorkspace {
  project_id: string;
  organization_packs: NexusPackCatalogItem[];
  personal_packs: NexusPersonalPack[];
  selected_organization_pack_id: number | null;
  selected_private_version_ids: number[];
}

export interface PluginPackFileContent {
  path: string;
  content: string;
}

export interface ProjectListResponse {
  projects: Project[];
  total: number;
}

export interface PlatformDescriptor {
  id: string;
  label: string;
  unit_label: string;
  unit_label_plural: string;
  badge_color: string;
  accent_color: string;
}

export interface PlatformListResponse {
  platforms: PlatformDescriptor[];
}

export interface ModelDescriptor {
  id: string;
  provider: string;
  label: string;
  supports_analysis: boolean;
  supports_writing: boolean;
  is_default_analysis: boolean;
  is_default_investigation: boolean;
  is_default_writing: boolean;
  is_local: boolean;
  available: boolean;
  unavailable_reason: string | null;
  rag_capable: boolean;
}

export interface ModelListResponse {
  models: ModelDescriptor[];
}

export interface StatusResponse {
  status: string;
  message: string;
}

export interface LogEvent {
  project_id: string;
  level: string;
  message: string;
}

export interface GenerateRun {
  id: number;
  project_id: string;
  triggered_by: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  cost_usd: number | null;
  duration_ms: number | null;
  duration_api_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  num_turns: number | null;
  session_id: string | null;
  doc_count: number;
  error_message: string | null;
  log_file: string | null;
  model_name: string | null;
  write_model_name: string | null;
  quality_score: number | null;
  auto_verdict: "PASS" | "FAIL" | null;
  auto_baseline_run_id: number | null;
  run_verdict: "draft" | "failed" | "published" | "published_with_rag_error" | "client_ready" | null;
  gate_results: Record<string, unknown> | null;
}

export interface ScanRun {
  id: number;
  project_id: string;
  triggered_by: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  job_count: number;
  log_file: string | null;
}

export interface ScanWarning {
  kind: string;
  file: string;
  message: string;
  phase: string;
}

export interface ScanStatusResponse {
  project_id: string;
  status: string;
  last_scan_at: string | null;
  cache_age_minutes: number | null;
  job_count: number;
  doc_count: number;
  error_message: string | null;
  previous_job_count: number | null;
  job_delta: number | null;
  warnings: ScanWarning[];
  warnings_count: number;
}

export interface DocFile {
  name: string;
  type: string;
  size_bytes: number;
  has_pdf: boolean;
  modified_at: string;
}

export interface LogFile {
  filename: string;
  project_id: string;
  timestamp: string;
  size_bytes: number;
}

export interface ProjectUsage {
  project_id: string;
  total_generate_runs: number;
  completed_generate_runs: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  total_scan_runs: number;
  completed_scan_runs: number;
}

export interface RunQualityDetails {
  overall?: number | null;
  [key: string]: unknown;
}

export interface RunComparisonRun {
  id: number;
  run_id: number;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  doc_count: number;
  provider: string | null;
  analysis_model: string | null;
  model_name: string | null;
  write_model: string | null;
  write_model_name: string | null;
  quality: number | null;
  quality_score: number | null;
  quality_pass: boolean;
  doc_quality: RunQualityDetails | null;
  cost_usd: number | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  num_turns: number | null;
  metrics_found: boolean;
  auto_verdict: "PASS" | "FAIL" | null;
  auto_baseline_run_id: number | null;
}

export interface RunComparison {
  project_id: string;
  run_a: RunComparisonRun;
  run_b: RunComparisonRun;
  delta: Record<string, number | null>;
  delta_pct: Record<string, number | null>;
  quality_delta: number | null;
  quality_delta_pct: number | null;
  verdict: "PASS" | "FAIL";
  checks: Array<Record<string, unknown>>;
}

export interface QualityHistoryEntry {
  run_id: number | null;
  started_at: string | null;
  analysis_model: string | null;
  write_model: string | null;
  quality: number | null;
  cost_usd: number | null;
  duration_ms: number | null;
  regression: boolean;
  quality_delta_pct: number | null;
  cost_regression: boolean;
  cost_delta_pct: number | null;
  duration_regression: boolean;
  duration_delta_pct: number | null;
}

export interface QualityHistoryResponse {
  project_id: string;
  history: QualityHistoryEntry[];
  total: number;
}

export interface ReviewQuestion {
  id: string;
  question: string;
  score: number;
}

export interface QuestionnaireResponse {
  project_id: string;
  run_id: number;
  questions: ReviewQuestion[];
}

export interface ReviewAggregateResponse {
  project_id: string;
  run_id: number | null;
  total_reviews: number;
  average_overall: number;
  average_per_question: Record<string, number>;
  pass_result: boolean;
  reviews: unknown[];
}

export interface InstructionPackMetadata {
  path: string;
  relative_path: string;
  version: string;
  exists: boolean;
  is_local: boolean;
  supported: boolean;
  preambles: Array<{ key: string; bytes: number; chars: number }>;
}

export interface AgenticExperimentArtifact {
  branch: string;
  kind: string;
  logical_name: string;
  relative_path: string;
  sha256: string;
  media_type: string;
  size_bytes: number;
}

export interface AgenticExperimentBranchMetrics {
  document_count: number;
  duration_ms: number | null;
  provider_latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  billed_cost_usd: string | null;
  quality_score: number | null;
  model_id: string | null;
  attempt_count: number | null;
  retry_count: number | null;
}

export interface AgenticExperiment {
  experiment_id: string;
  project_id: string;
  baseline_run_id: number | null;
  status: string;
  comparison_status: string;
  candidate_verdict: string;
  runtime_id: string;
  provider_id: string | null;
  model_id: string | null;
  source_snapshot_id: string | null;
  source_snapshot_frozen: boolean;
  recipe_snapshot_id: string | null;
  recipe_id: string | null;
  recipe_version: string | null;
  baseline_manifest_sha256: string | null;
  baseline_document_count: number;
  artifact_count: number;
  publishes: false;
  candidate_not_started: boolean;
  error_message: string | null;
  baseline_metrics: AgenticExperimentBranchMetrics;
  candidate_metrics: AgenticExperimentBranchMetrics;
  safety_warnings: {
    path: string;
    category: string;
    severity: string;
  }[];
  grounded_progress: {
    stage: string;
    accepted_artifact_count: number;
    required_artifact_count: number;
    roles: {
      role_id: string;
      stage: string;
      status: "pending" | "completed";
      accepted_artifact_count: number;
      owned_artifact_count: number;
    }[];
  } | null;
  artifacts: AgenticExperimentArtifact[];
}

export interface AgenticRecipeSourceRequirement {
  source_role: string;
  required_capabilities: string[];
  optional: boolean;
}

export interface AgenticRecipeCapability {
  recipe_id: string;
  recipe_version: string;
  source_requirements: AgenticRecipeSourceRequirement[];
  required_model_capabilities: string[];
  output_ids: string[];
  document_output_count: number;
}

export interface AgenticExperimentCapabilities {
  feature_enabled: true;
  provider_neutral: true;
  model_selection: "runtime_discovered";
  publishes: false;
  candidate_dispatch_available: boolean;
  paid_execution_enabled: boolean;
  opencode_version: string;
  supported_source_kinds: string[];
  recipes: AgenticRecipeCapability[];
  execution_blockers: string[];
}

export interface AgenticSourceInput {
  id: string;
  source_kind: "intodoc_scan" | "directory_evidence" | "tabular_file" | "runtime_export";
  source_role: string;
  capabilities: string[];
  governed_root_index?: number;
  relative_path?: string;
}

export interface AgenticSourceSnapshot {
  experiment_id: string;
  snapshot_id: string;
  manifest_sha256: string;
  security_state: "approved";
  manifest_relative_path: string;
  item_count: number;
  source_kinds: string[];
  source_roles: string[];
  publishes: false;
}

export interface AgenticRecipeSnapshot {
  experiment_id: string;
  snapshot_id: string;
  recipe_id: string;
  recipe_version: string;
  graph_sha256: string;
  effective_scope_sha256: string;
  manifest_relative_path: string;
  task_count: number;
  document_output_count: number;
  preflight_required: true;
  publishes: false;
}

export interface AgenticTaskAttempt {
  attempt_id: string;
  attempt_number: number;
  status: string;
  started_at: string;
  completed_at: string | null;
  wall_duration_ms: number | null;
  provider_latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  provider_cost_usd: string | null;
}

export interface AgenticTaskEvent {
  event_id: number;
  attempt_id: string | null;
  sequence_number: number;
  event_type: string;
  occurred_at: string;
}

export interface AgenticExperimentTask {
  task_run_id: string;
  node_id: string;
  task_type: string;
  role: string;
  dependencies: string[];
  output_ids: string[];
  status: string;
  attempt: number;
  max_attempts: number;
  runtime_id: string | null;
  provider_id: string | null;
  model_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  attempts: AgenticTaskAttempt[];
  events: AgenticTaskEvent[];
  publishes: false;
}

export interface AgenticExperimentTaskList {
  experiment_id: string;
  tasks: AgenticExperimentTask[];
  total: number;
  publishes: false;
}

export interface AgenticReconcileResult {
  experiment_id: string;
  persisted_status: string;
  derived_status: string;
  recovered_expired_task_ids: string[];
  manual_retry_task_ids: string[];
  missing_required_outputs: string[];
  duplicate_required_outputs: string[];
  tasks: Array<{
    task_run_id: string;
    state: string;
    manual_retry_required: boolean;
    reason: string | null;
  }>;
  dispatch_performed: false;
  publishes: false;
}

export interface AgenticRetentionResult {
  experiment_id: string;
  project_id: string;
  state: "quarantined" | "restored";
  receipt_sha256: string;
  source_relative_path: string;
  trash_relative_path: string;
  hard_deleted: false;
  publishes: false;
}
