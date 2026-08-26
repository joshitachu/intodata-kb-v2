export type DocSource = "published" | "staging";

/**
 * How a project's git credential is resolved, as reported by the API.
 *
 * `string & {}` keeps autocomplete for the known kinds while still accepting a
 * value this build has not been taught yet: the API is free to add one, and the
 * dashboard must degrade to its default branch rather than reject the project.
 */
export type ConnectionKind = "pat" | "app" | "azure_devops" | (string & {});

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
  connection_kind: ConnectionKind;
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
  usable: boolean;
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

/** Safe, provider-neutral connector metadata. Credentials are intentionally absent. */
export interface ConnectorCapability {
  id: string;
  label: string;
  description: string | null;
  read_only: boolean;
  gather_eligible: boolean;
}

export interface ConnectorProvider {
  provider_id: string;
  label: string;
  version: string | null;
  authentication_types: string[];
  capabilities: ConnectorCapability[];
  enabled: boolean;
  /**
   * WHICH LANE this provider feeds, and therefore which engine reads it.
   *
   * `"live_evidence"` is queried in-band during a run; `"runtime_sync"` is
   * mirrored on a schedule and read back afterwards. Presentation is derived
   * from this field alone -- see `lib/connectors.ts`. Nothing in the UI may
   * branch on `provider_id` to decide how a source behaves.
   *
   * `string & {}` for the same reason as `ConnectionKind`: a kind this build
   * has not been taught must render as unknown, never reject the catalogue
   * parse and blank the page.
   */
  kind: "live_evidence" | "runtime_sync" | (string & {});
  build_state: "available" | "coming_soon" | (string & {});
  /**
   * WHERE this provider's credential is stored -- not who may write it.
   *
   * `"connector_store"` is the MCP server's own encrypted store;
   * `"runtime_sources"` is the RAG backend's tables, next to the scheduler that
   * polls with the credential every 60 seconds. Both are connectable from this
   * page; the registered dialog knows which lane to post to. The field exists so
   * the UI can say where a secret ended up, and so a reader of this code knows
   * that two different stores are in play.
   *
   * `string & {}` for the same reason as `ConnectionKind`: a value this build
   * has not been taught yet must render as an unfamiliar storage location, never
   * reject the catalogue parse and blank the page.
   */
  managed_by: "connector_store" | "runtime_sources" | (string & {});
  description: string;
}

export type ConnectorStatus = "connected" | "degraded" | "not_connected" | "disabled" | "unknown";

export interface ConnectorInstance {
  id: string;
  provider_id: string;
  display_name: string;
  ownership_scope: string;
  enabled: boolean;
  status: ConnectorStatus;
  last_checked_at: string | null;
  health_code: string | null;
  provider_version: string | null;
  redaction_policy_version: string | null;
  has_credential: boolean;
  has_kibana_credential: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ConnectorSource {
  id: string;
  connector_instance_id: string;
  source_type: string;
  display_label: string;
  enabled: boolean;
  field_profile: Record<string, unknown>;
  discovered_at: string | null;
}

export interface KibanaSpace { id: string; name: string; }
export interface KibanaDataView { id: string; title: string; }

/** Project-management scoped view: safe connector metadata plus only its sources. */
export interface ProjectAvailableConnector {
  connector: ConnectorInstance;
  sources: ConnectorSource[];
}

export interface ProjectConnectorBinding {
  id: string;
  project_id: string;
  connector_instance_id: string;
  connector_display_name: string | null;
  connector_provider_id: string | null;
  source_ids: string[];
  capability_ids: string[];
  budgets: Record<string, number>;
  max_time_window_days: number | null;
  enabled: boolean;
  version: number;
}

export interface ConnectorTestResult {
  status: string;
  message: string | null;
  checked_at: string | null;
  details: Record<string, unknown>;
}

/** One input in a runtime provider's credential form, as the provider declares it. */
export interface RuntimeSourceCredentialField {
  name: string;
  label: string;
  /** `secret` renders write-only, `url` validates as a URL, anything else is text. */
  type: "secret" | "url" | "text" | (string & {});
  required: boolean;
}

/** A provider's selectable ingest scope, e.g. Talend environments. */
export interface RuntimeScopeSelection {
  /** Credential key that stores the chosen ids. */
  field: string;
  label: string;
  description?: string | null;
  required: boolean;
  multiple: boolean;
}

/** One scope a live connection reported as reachable. */
export interface RuntimeScopeOption {
  id: string;
  label: string;
  /** Free-text hint such as a task count; cosmetic, may be absent. */
  detail?: string | null;
  default_selected: boolean;
}

export interface RuntimeSourceType {
  /** The RAG backend's runtime key (e.g. `tmc`), not the connector id. */
  provider_key: string;
  provider_label: string;
  credential_schema: RuntimeSourceCredentialField[];
  /** Absent for providers that ingest the whole source. */
  scope_selection?: RuntimeScopeSelection | null;
}

/**
 * A credential the RAG backend's scheduler polls, bound to exactly one project.
 *
 * No credential field: secrets are write-only and no server returns them.
 */
export interface RuntimeSource {
  id: number;
  project_id: string;
  source_type: string;
  label: string;
  /** `success` | `failed` | `pending` | ... -- open, so a new value renders as itself. */
  status: string;
  enabled: boolean;
  sync_interval_seconds: number;
  disabled_reason: string | null;
  last_synced_at: string | null;
}

/**
 * What a documentation run for one project reads out of its runtime mirror.
 *
 * Set per project in the RAG app; shown here because it changes what a run
 * produces and an operator should see it before starting one.
 */
export interface RuntimeDocReadScope {
  /** null means every mirrored environment. */
  environment_ids: string[] | null;
  environment_names: string[];
  window_days: number | null;
  configured: boolean;
  unavailable_environment_ids: string[];
}

export interface RuntimeSourceSyncResult {
  source: RuntimeSource | null;
  status: string;
  records_ingested: number;
}

/**
 * One project's permission to read a runtime mirror it does not own.
 *
 * One Talend tenant commonly backs several scanned projects, and registering
 * its credential twice is refused -- that would poll the vendor twice and leave
 * two mirrors of one estate free to drift apart. A second project therefore
 * SHARES the existing mirror through a grant.
 *
 * Not to be confused with a workspace mapping, which SPLITS one source's rows
 * between projects at write time. Share vs split: different answers to
 * different questions.
 */
export interface RuntimeSourceReadGrant {
  id: number;
  source_id: number;
  /** The reader. The source's own project is the grantor and never appears. */
  project_id: string;
  source_project_id: string;
  source_label: string;
  /** null means every mirrored environment. */
  environment_ids: string[] | null;
  window_days: number | null;
  /**
   * Every project whose rows this grant exposes -- the source's own project
   * plus any project its workspace map re-points rows to. Shown so a grant can
   * never widen past what the operator intended without saying so.
   */
  exposes_project_ids: string[];
}

export interface ConnectorEvidenceFinding {
  evidence_id: string;
  classification: "corroborates" | "operational_context" | "discrepancy" | "inconclusive" | string;
  summary: string | null;
}

/**
 * Coverage of the mirrored runtime lane (Talend Management Console today).
 *
 * Read server-side from the project's published
 * `_assessment/runtime_evidence_coverage.json`, so it describes the LATEST
 * PUBLISHED DOCUMENTS rather than the historical run being inspected. The
 * popover says so; per-run archival is deferred.
 */
export interface RunRuntimeEvidenceSummary {
  available: boolean;
  /** data | opted_out | unavailable | partial | binding_suspect, plus room for a newer writer. */
  mode: string;
  window_days: number | null;
  units_requested: number;
  units_matched: number;
  /** A count, not names: orphan job names are customer-identifying. */
  orphan_runtime_job_count: number;
  partial: boolean;
  /**
   * The runtime source lists many jobs with no scanned counterpart. Computed
   * server-side from an orphan-ratio threshold, so the client reads it instead
   * of re-deriving it.
   *
   * `null` means the payload predates the field (older coverage artifact); only
   * then may the client fall back to inferring the signal. A reported `false`
   * is a denial and must silence the fallback.
   */
  scope_mismatch: boolean | null;
  batches: number | null;
  source_key: string | null;
  /**
   * Composite (partitioned) upload: the sub-project this figure describes.
   * Null on a single-project run and on the aggregate of a composite upload.
   */
  subproject: string | null;
  /** Sub-projects an aggregate spans. 0 on a single, non-composite artifact. */
  subproject_count: number;
}

export interface ConnectorEvidenceSummary {
  run_id: number;
  available: boolean;
  connector_count: number;
  successful_calls: number;
  failed_calls: number;
  denied_calls: number;
  truncated_calls: number;
  observation_from: string | null;
  observation_to: string | null;
  limitations: string[];
  findings: ConnectorEvidenceFinding[];
  /** Headline coverage. On a composite upload this is the aggregate. */
  runtime: RunRuntimeEvidenceSummary;
  /**
   * The per-sub-project rows the aggregate was built from. Empty for a
   * non-composite run and for a composite child run.
   */
  runtime_subprojects: RunRuntimeEvidenceSummary[];
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
  partition_name?: string | null;
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
