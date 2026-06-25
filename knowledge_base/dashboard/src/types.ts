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
  path: string;
  description: string | null;
  repo_url: string | null;
  repo_branch: string | null;
  last_commit_sha: string | null;
  auto_update: boolean;
}

export interface StarterPack {
  zip_url: string;
  repo_url: string | null;
  clone_command: string | null;
}

export interface PluginPackBranch {
  branch: string;
  commit_sha: string | null;
  has_pack: boolean;
  valid: boolean;
  name: string | null;
  version: string | null;
  description: string | null;
  registered_pack_id: number | null;
  error: string | null;
}

export interface PluginPackBranchDiscovery {
  repo_url: string;
  branches: PluginPackBranch[];
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
