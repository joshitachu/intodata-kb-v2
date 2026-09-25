import { z } from "zod";

// Runtime logs API (EP-005 slice 2 backend contract). Datetimes arrive as ISO-8601
// strings in JSON, mirroring RuntimeStatusResponse.checked_at.

// "misfired" (a scheduled run that never started), "timed_out" and
// "terminated" widen the original four-value outcome so TMC's real execution
// vocabulary stops being flattened into "unknown". Every schema below that
// re-uses this enum (job/engine/plan health, deployments, plan-run steps)
// picks up the wider set automatically -- widen it once, here.
export const RuntimeExecutionStateSchema = z.enum([
  "success",
  "failed",
  "misfired",
  "timed_out",
  "terminated",
  "running",
  "unknown",
]);
export type RuntimeExecutionState = z.infer<typeof RuntimeExecutionStateSchema>;

export const RuntimeExecutableKindSchema = z.enum(["job", "route", "data_service", "unknown"]);
export type RuntimeExecutableKind = z.infer<typeof RuntimeExecutableKindSchema>;

// What a run row MEANS. An "execution" row is one run of a schedulable unit: it
// starts, it ends, and `state` is that run's outcome. A "lifecycle" row is a
// DEPLOYMENT that stays up -- `running` means "deployed and up", not "busy", and
// its row count is a redeploy count, not a run count. Reading a lifecycle row
// with execution semantics is how 68 deployed Pluripharm routes read as
// not-healthy. Absent on an older backend; consumers fall back to execution
// semantics (see lib/jobHealth.ts deriveStatus).
export const RuntimeLivenessSchema = z.enum(["execution", "lifecycle", "unknown"]);
export type RuntimeLiveness = z.infer<typeof RuntimeLivenessSchema>;

export const RuntimeLogSeveritySchema = z.enum([
  "debug",
  "info",
  "warning",
  "error",
]);
export type RuntimeLogSeverity = z.infer<typeof RuntimeLogSeveritySchema>;

export const RuntimeExecutionSchema = z.object({
  execution_id: z.string(),
  job_name: z.string(),
  project_name: z.string().nullable().optional(),
  state: RuntimeExecutionStateSchema,
  executable_kind: RuntimeExecutableKindSchema.optional().default("job"),
  started_at: z.string(),
  ended_at: z.string().nullable().optional(),
  duration_ms: z.number().nullable().optional(),
  // The verbatim TMC status (e.g. "deploy_failed"), kept alongside the
  // normalized `state` so a reader can always see what the source actually
  // said.
  raw_state: z.string().nullable().optional(),
  // Populated only when state === "misfired". One of
  // PARALLEL_EXECUTION_FORBIDDEN / TASK_IS_PAUSED /
  // MAX_DEPLOYMENT_ATTEMPTS_REACHED / UNKNOWN.
  misfire_reason: z.string().nullable().optional(),
  // MANUAL / SCHEDULED / PLAN. Populated on every record; the customer's
  // per-view axis for "who or what triggered this run".
  execution_type: z.string().nullable().optional(),
});
export type RuntimeExecution = z.infer<typeof RuntimeExecutionSchema>;

export const RuntimeFailureSchema = z.object({
  execution_id: z.string(),
  job_name: z.string(),
  failed_at: z.string(),
  error_summary: z.string(),
});
export type RuntimeFailure = z.infer<typeof RuntimeFailureSchema>;

export const RuntimeLogEventSchema = z.object({
  execution_id: z.string(),
  timestamp: z.string(),
  severity: RuntimeLogSeveritySchema,
  message: z.string(),
  // Position is part of a line's identity: repeated messages are valid log data,
  // so consumers must key on (execution_id, line_ordinal), not on the message.
  line_ordinal: z.number().optional().default(0),
  scope: z.enum(["execution", "engine"]).optional().default("execution"),
  attribution_safe: z.boolean().optional().default(true),
});
export type RuntimeLogEvent = z.infer<typeof RuntimeLogEventSchema>;

export const ExecutionListResponseSchema = z.object({
  executions: z.array(RuntimeExecutionSchema),
  total: z.number(),
  offset: z.number().optional().default(0),
  limit: z.number().optional().default(0),
});
export type ExecutionListResponse = z.infer<typeof ExecutionListResponseSchema>;

export const ExecutionDetailResponseSchema = z.object({
  execution: RuntimeExecutionSchema,
});
export type ExecutionDetailResponse = z.infer<typeof ExecutionDetailResponseSchema>;

export const LogEventListResponseSchema = z.object({
  execution_id: z.string(),
  events: z.array(RuntimeLogEventSchema),
  total: z.number(),
});
export type LogEventListResponse = z.infer<typeof LogEventListResponseSchema>;

export const FailureListResponseSchema = z.object({
  failures: z.array(RuntimeFailureSchema),
  total: z.number(),
});
export type FailureListResponse = z.infer<typeof FailureListResponseSchema>;

export const RuntimeHealthSummarySchema = z.object({
  project_id: z.string(),
  source_key: z.string().nullable().optional(),
  job_name: z.string(),
  total_executions: z.number(),
  failed_executions: z.number(),
  failure_rate: z.number(),
  current_failure_streak: z.number(),
  last_failure_at: z.string().nullable().optional(),
  last_execution_at: z.string().nullable().optional(),
  p50_duration_ms: z.number().nullable().optional(),
  p95_duration_ms: z.number().nullable().optional(),
  rows_total: z.number().nullable().optional(),
  rows_rejected: z.number().nullable().optional(),
  reject_ratio: z.number().nullable().optional(),
  recurrence_count: z.number().nullable().optional(),
  signature_hash: z.string().nullable().optional(),
});
export type RuntimeHealthSummary = z.infer<typeof RuntimeHealthSummarySchema>;

export const HealthSummaryResponseSchema = z.object({
  health: z.array(RuntimeHealthSummarySchema),
  total: z.number(),
});
export type HealthSummaryResponse = z.infer<typeof HealthSummaryResponseSchema>;

export const RuntimeSyncStatusSchema = z.enum(["success", "partial", "failed"]);
export type RuntimeSyncStatus = z.infer<typeof RuntimeSyncStatusSchema>;

export const RuntimeEngineStatusSchema = z.enum([
  "online",
  "offline",
  "degraded",
  "unknown",
  "stale",
]);
export type RuntimeEngineStatus = z.infer<typeof RuntimeEngineStatusSchema>;

/**
 * One registered runtime source (a mirrored vendor tenant).
 *
 * A source has NO owning project. It has an `estate_key` -- the stamp its
 * mirrored rows carry -- and a set of ATTACHED projects that may read it.
 * The old `project_id` field conflated the two: it was read as "the owning
 * project" while it was really the row stamp, which is why registering a second
 * tenant for a project was impossible. It is gone on purpose; nothing here may
 * reintroduce it.
 */
export const RuntimeSourceSchema = z.object({
  id: z.number().optional(),
  /**
   * Internal stamp, NOT a project id. Never render it as one, and never parse
   * it to decide whether a source has projects -- `is_ownerless` answers that.
   */
  estate_key: z.string(),
  /** Attached projects, sorted. `[]` is a real state, not a missing value. */
  linked_project_ids: z.array(z.string()),
  /**
   * True when no project has ever been attached. Declared (and required) rather
   * than derived: a source registered before any project is a normal state that
   * the UI has to name, and inferring it from `estate_key` is exactly the
   * string-sniffing this field exists to replace.
   */
  is_ownerless: z.boolean(),
  source_key: z.string(),
  source_type: z.string(),
  label: z.string(),
  status: z.string(),
  enabled: z.boolean().optional(),
  sync_interval_seconds: z.number().optional(),
  auto_disable_threshold: z.number().optional(),
  disabled_reason: z.string().nullable().optional(),
  is_demo: z.boolean().optional().default(false),
  last_synced_at: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
  ingested_at: z.string(),
  health: z
    .object({
      last_attempt_at: z.string().nullable().optional(),
      last_success_at: z.string().nullable().optional(),
      last_failure_at: z.string().nullable().optional(),
      consecutive_failures: z.number().optional().default(0),
      last_failure_sanitized: z.string().nullable().optional(),
      total_cycles: z.number().optional().default(0),
      total_records_ingested: z.number().optional().default(0),
    })
    .nullable()
    .optional(),
});
export type RuntimeSource = z.infer<typeof RuntimeSourceSchema>;

export const RuntimeEngineSchema = z.object({
  engine_id: z.string(),
  source_key: z.string(),
  name: z.string(),
  status: RuntimeEngineStatusSchema,
  raw_status: z.string().nullable().optional(),
  availability: z.string().nullable().optional(),
  version: z.string().nullable().optional(),
  environment_id: z.string().nullable().optional(),
  environment_name: z.string().nullable().optional(),
  workspace_id: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
  source_updated_at: z.string().nullable().optional(),
});
export type RuntimeEngine = z.infer<typeof RuntimeEngineSchema>;

export const RuntimeEstateOverviewSchema = z.object({
  project_id: z.string().nullable().optional(),
  sources_total: z.number(),
  sources_failed: z.number(),
  sources_partial: z.number(),
  sources_demo: z.number(),
  engines_total: z.number(),
  engines_online: z.number(),
  engines_degraded: z.number(),
  engines_offline: z.number(),
  engines_unknown: z.number(),
  engines_unhealthy: z.number().optional().default(0),
  jobs_unhealthy: z.number().optional().default(0),
  plans_unhealthy: z.number().optional().default(0),
  sources_stale: z.number().optional().default(0),
  environments_total: z.number(),
  workspaces_total: z.number(),
  last_synced_at: z.string().nullable().optional(),
});
export type RuntimeEstateOverview = z.infer<typeof RuntimeEstateOverviewSchema>;

export const RuntimeSourceListResponseSchema = z.object({
  sources: z.array(RuntimeSourceSchema),
  total: z.number(),
});
export type RuntimeSourceListResponse = z.infer<typeof RuntimeSourceListResponseSchema>;

export const RuntimeEngineListResponseSchema = z.object({
  engines: z.array(RuntimeEngineSchema),
  total: z.number(),
  offset: z.number().optional().default(0),
  limit: z.number().optional().default(0),
});
export type RuntimeEngineListResponse = z.infer<typeof RuntimeEngineListResponseSchema>;

export const RuntimeOverviewResponseSchema = z.object({
  overview: RuntimeEstateOverviewSchema,
});
export type RuntimeOverviewResponse = z.infer<typeof RuntimeOverviewResponseSchema>;

export const RuntimeJobSchema = z.object({
  job_id: z.string(),
  source_key: z.string(),
  name: z.string(),
  display_name: z.string().nullable().optional(),
  job_type: z.string().nullable().optional(),
  executable_kind: RuntimeExecutableKindSchema.optional().default("job"),
  status: z.string().nullable().optional(),
  environment_id: z.string().nullable().optional(),
  environment_name: z.string().nullable().optional(),
  workspace_id: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  engine_id: z.string().nullable().optional(),
  engine_name: z.string().nullable().optional(),
  owner: z.string().nullable().optional(),
  artifact_name: z.string().nullable().optional(),
  artifact_id: z.string().nullable().optional(),
  artifact_version: z.string().nullable().optional(),
  metric_path: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
  source_updated_at: z.string().nullable().optional(),
});
export type RuntimeJob = z.infer<typeof RuntimeJobSchema>;

export const RuntimeJobRunSchema = z.object({
  run_id: z.string(),
  job_id: z.string(),
  job_name: z.string(),
  source_key: z.string(),
  execution_id: z.string().nullable().optional(),
  state: RuntimeExecutionStateSchema,
  executable_kind: RuntimeExecutableKindSchema.optional().default("job"),
  environment_id: z.string().nullable().optional(),
  environment_name: z.string().nullable().optional(),
  workspace_id: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  engine_id: z.string().nullable().optional(),
  engine_name: z.string().nullable().optional(),
  started_at: z.string(),
  ended_at: z.string().nullable().optional(),
  duration_ms: z.number().nullable().optional(),
  completed_exchanges: z.number().nullable().optional(),
  failed_exchanges: z.number().nullable().optional(),
  total_exchanges: z.number().nullable().optional(),
  last_process_time_ms: z.number().nullable().optional(),
  mean_process_time_ms: z.number().nullable().optional(),
  metrics_updated_at: z.string().nullable().optional(),
  metric_path: z.string().nullable().optional(),
  service_call_count: z.number().nullable().optional(),
  one_minute_rate: z.number().nullable().optional(),
  five_minute_rate: z.number().nullable().optional(),
  fifteen_minute_rate: z.number().nullable().optional(),
  // See RuntimeExecutionSchema.raw_state / misfire_reason / execution_type.
  raw_state: z.string().nullable().optional(),
  misfire_reason: z.string().nullable().optional(),
  execution_type: z.string().nullable().optional(),
});
export type RuntimeJobRun = z.infer<typeof RuntimeJobRunSchema>;

export const RuntimeDeploymentSchema = z.object({
  project_id: z.string(),
  source_key: z.string(),
  deployment_id: z.string().nullable().optional(),
  executable_id: z.string(),
  executable_name: z.string(),
  executable_kind: RuntimeExecutableKindSchema,
  state: RuntimeExecutionStateSchema.nullable().optional(),
  environment_name: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  engine_name: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  ended_at: z.string().nullable().optional(),
  duration_ms: z.number().nullable().optional(),
  completed_exchanges: z.number().nullable().optional(),
  failed_exchanges: z.number().nullable().optional(),
  total_exchanges: z.number().nullable().optional(),
  last_process_time_ms: z.number().nullable().optional(),
  mean_process_time_ms: z.number().nullable().optional(),
  metrics_updated_at: z.string().nullable().optional(),
  metric_path: z.string().nullable().optional(),
  service_call_count: z.number().nullable().optional(),
  one_minute_rate: z.number().nullable().optional(),
  five_minute_rate: z.number().nullable().optional(),
  fifteen_minute_rate: z.number().nullable().optional(),
});
export type RuntimeDeployment = z.infer<typeof RuntimeDeploymentSchema>;

export const RuntimeDeploymentListResponseSchema = z.object({
  deployments: z.array(RuntimeDeploymentSchema),
  total: z.number(),
});
export type RuntimeDeploymentListResponse = z.infer<typeof RuntimeDeploymentListResponseSchema>;

export const RuntimeJobHealthSchema = z.object({
  project_id: z.string(),
  source_key: z.string(),
  job_id: z.string(),
  job_name: z.string(),
  environment_name: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  engine_name: z.string().nullable().optional(),
  total_runs: z.number(),
  failed_runs: z.number(),
  failure_rate: z.number(),
  current_failure_streak: z.number(),
  latest_run_at: z.string().nullable().optional(),
  latest_state: RuntimeExecutionStateSchema.nullable().optional(),
  // Additive, and `.optional()` rather than `.default()` on purpose: a
  // `.default()` is applied at parse time but makes the field REQUIRED in the
  // inferred type, which breaks every hand-built RuntimeJobHealth literal in
  // the tests and in lib/businessHealth.ts consumers. See the same note on
  // `BusinessDataStreamSchema.mechanism` in types/landscape.ts.
  //
  // `artifact_name` is the DESIGN the runtime row was deployed from, which is
  // what a published topology's artifact annotations carry. `job_name` is the
  // name the scheduler happens to use and the two differ often enough that
  // matching on the task name alone is the reason the join needed a fuzzy tier.
  artifact_name: z.string().nullable().optional(),
  executable_kind: RuntimeExecutableKindSchema.optional(),
  liveness: RuntimeLivenessSchema.optional(),
});
export type RuntimeJobHealth = z.infer<typeof RuntimeJobHealthSchema>;

export const RuntimeEngineHealthSchema = z.object({
  project_id: z.string(),
  source_key: z.string(),
  engine_id: z.string(),
  engine_name: z.string(),
  engine_status: RuntimeEngineStatusSchema.nullable().optional(),
  environment_name: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  total_runs: z.number(),
  failed_runs: z.number(),
  failure_rate: z.number(),
  current_failure_streak: z.number(),
  latest_run_at: z.string().nullable().optional(),
  latest_state: RuntimeExecutionStateSchema.nullable().optional(),
  p50_duration_ms: z.number().nullable().optional(),
  p95_duration_ms: z.number().nullable().optional(),
});
export type RuntimeEngineHealth = z.infer<typeof RuntimeEngineHealthSchema>;

export const RuntimeJobListResponseSchema = z.object({
  jobs: z.array(RuntimeJobSchema),
  total: z.number(),
  offset: z.number().optional().default(0),
  limit: z.number().optional().default(0),
});
export type RuntimeJobListResponse = z.infer<typeof RuntimeJobListResponseSchema>;

export const RuntimeArtifactSchema = z.object({
  artifact_id: z.string(),
  source_key: z.string(),
  name: z.string(),
  artifact_type: z.string().nullable().optional(),
  version: z.string().nullable().optional(),
  workspace_id: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  owner: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
  source_updated_at: z.string().nullable().optional(),
});
export type RuntimeArtifact = z.infer<typeof RuntimeArtifactSchema>;

export const RuntimeConnectionSchema = z.object({
  connection_id: z.string(),
  source_key: z.string(),
  name: z.string(),
  connection_type: z.string().nullable().optional(),
  workspace_id: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  owner: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
  source_updated_at: z.string().nullable().optional(),
});
export type RuntimeConnection = z.infer<typeof RuntimeConnectionSchema>;

export const RuntimeJobDependencySchema = z.object({
  job_id: z.string(),
  job_name: z.string(),
  source_key: z.string(),
  dependency_type: z.string(),
  dependency_id: z.string(),
  dependency_name: z.string().nullable().optional(),
  dependency_kind: z.string().nullable().optional(),
  workspace_id: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
});
export type RuntimeJobDependency = z.infer<typeof RuntimeJobDependencySchema>;

export const RuntimeExtendedMetadataAvailabilitySchema = z.object({
  artifacts_supported: z.boolean().optional().default(false),
  connections_supported: z.boolean().optional().default(false),
  schedules_supported: z.boolean().optional().default(false),
  promotions_supported: z.boolean().optional().default(false),
  clusters_supported: z.boolean().optional().default(false),
  unavailable: z.array(z.string()).optional().default([]),
});
export type RuntimeExtendedMetadataAvailability = z.infer<
  typeof RuntimeExtendedMetadataAvailabilitySchema
>;

export const RuntimeDependencySummarySchema = z.object({
  availability: RuntimeExtendedMetadataAvailabilitySchema,
  artifacts_total: z.number(),
  connections_total: z.number(),
  dependencies_total: z.number(),
  stale_artifacts: z.array(RuntimeArtifactSchema).optional().default([]),
  unowned_artifacts: z.array(RuntimeArtifactSchema).optional().default([]),
  unowned_connections: z.array(RuntimeConnectionSchema).optional().default([]),
});
export type RuntimeDependencySummary = z.infer<typeof RuntimeDependencySummarySchema>;

export const RuntimeArtifactListResponseSchema = z.object({
  artifacts: z.array(RuntimeArtifactSchema),
  total: z.number(),
});
export type RuntimeArtifactListResponse = z.infer<typeof RuntimeArtifactListResponseSchema>;

export const RuntimeConnectionListResponseSchema = z.object({
  connections: z.array(RuntimeConnectionSchema),
  total: z.number(),
});
export type RuntimeConnectionListResponse = z.infer<typeof RuntimeConnectionListResponseSchema>;

export const RuntimeDependencyListResponseSchema = z.object({
  dependencies: z.array(RuntimeJobDependencySchema),
  total: z.number(),
});
export type RuntimeDependencyListResponse = z.infer<typeof RuntimeDependencyListResponseSchema>;

export const RuntimeDependencySummaryResponseSchema = z.object({
  summary: RuntimeDependencySummarySchema,
});
export type RuntimeDependencySummaryResponse = z.infer<
  typeof RuntimeDependencySummaryResponseSchema
>;

export const RuntimeJobRunListResponseSchema = z.object({
  runs: z.array(RuntimeJobRunSchema),
  total: z.number(),
});
export type RuntimeJobRunListResponse = z.infer<typeof RuntimeJobRunListResponseSchema>;

export const RuntimeJobHealthResponseSchema = z.object({
  health: z.array(RuntimeJobHealthSchema),
  total: z.number(),
});
export type RuntimeJobHealthResponse = z.infer<typeof RuntimeJobHealthResponseSchema>;

export const RuntimeEngineHealthResponseSchema = z.object({
  health: z.array(RuntimeEngineHealthSchema),
  total: z.number(),
});
export type RuntimeEngineHealthResponse = z.infer<typeof RuntimeEngineHealthResponseSchema>;

export const RuntimePlanStepSchema = z.object({
  step_id: z.string(),
  name: z.string(),
  job_id: z.string().nullable().optional(),
  job_name: z.string().nullable().optional(),
  order: z.number().nullable().optional(),
});
export type RuntimePlanStep = z.infer<typeof RuntimePlanStepSchema>;

export const RuntimePlanSchema = z.object({
  plan_id: z.string(),
  source_key: z.string(),
  name: z.string(),
  display_name: z.string().nullable().optional(),
  status: z.string().nullable().optional(),
  environment_id: z.string().nullable().optional(),
  environment_name: z.string().nullable().optional(),
  workspace_id: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  owner: z.string().nullable().optional(),
  step_count: z.number().nullable().optional(),
  steps: z.array(RuntimePlanStepSchema).optional().default([]),
  last_seen_at: z.string().nullable().optional(),
  source_updated_at: z.string().nullable().optional(),
});
export type RuntimePlan = z.infer<typeof RuntimePlanSchema>;

export const RuntimePlanRunStepResultSchema = z.object({
  step_id: z.string(),
  job_id: z.string().nullable().optional(),
  job_name: z.string().nullable().optional(),
  state: RuntimeExecutionStateSchema.nullable().optional(),
  execution_id: z.string().nullable().optional(),
});
export type RuntimePlanRunStepResult = z.infer<typeof RuntimePlanRunStepResultSchema>;

export const RuntimePlanRunSchema = z.object({
  run_id: z.string(),
  plan_id: z.string(),
  plan_name: z.string(),
  source_key: z.string(),
  state: RuntimeExecutionStateSchema,
  environment_id: z.string().nullable().optional(),
  environment_name: z.string().nullable().optional(),
  workspace_id: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  started_at: z.string(),
  ended_at: z.string().nullable().optional(),
  duration_ms: z.number().nullable().optional(),
  step_results: z.array(RuntimePlanRunStepResultSchema).optional().default([]),
  // See RuntimeExecutionSchema.raw_state / misfire_reason / execution_type.
  raw_state: z.string().nullable().optional(),
  misfire_reason: z.string().nullable().optional(),
  execution_type: z.string().nullable().optional(),
});
export type RuntimePlanRun = z.infer<typeof RuntimePlanRunSchema>;

export const RuntimePlanHealthSchema = z.object({
  project_id: z.string(),
  source_key: z.string(),
  plan_id: z.string(),
  plan_name: z.string(),
  environment_name: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  total_runs: z.number(),
  failed_runs: z.number(),
  failure_rate: z.number(),
  current_failure_streak: z.number(),
  latest_run_at: z.string().nullable().optional(),
  latest_finish_at: z.string().nullable().optional(),
  latest_state: RuntimeExecutionStateSchema.nullable().optional(),
});
export type RuntimePlanHealth = z.infer<typeof RuntimePlanHealthSchema>;

export const RuntimePlanListResponseSchema = z.object({
  plans: z.array(RuntimePlanSchema),
  total: z.number(),
  offset: z.number().optional().default(0),
  limit: z.number().optional().default(0),
});
export type RuntimePlanListResponse = z.infer<typeof RuntimePlanListResponseSchema>;

export const RuntimePlanRunListResponseSchema = z.object({
  runs: z.array(RuntimePlanRunSchema),
  total: z.number(),
});
export type RuntimePlanRunListResponse = z.infer<typeof RuntimePlanRunListResponseSchema>;

export const RuntimePlanHealthResponseSchema = z.object({
  health: z.array(RuntimePlanHealthSchema),
  total: z.number(),
});
export type RuntimePlanHealthResponse = z.infer<typeof RuntimePlanHealthResponseSchema>;

// --- Smart-log insight schemas (EP-006 slice 2 backend contract) ---

export const RuntimeFailureEstateContextSchema = z.object({
  source_key: z.string().nullable().optional(),
  source_label: z.string().nullable().optional(),
  source_type: z.string().nullable().optional(),
  source_status: z.string().nullable().optional(),
  source_is_demo: z.boolean().nullable().optional(),
  source_last_synced_at: z.string().nullable().optional(),
  source_last_seen_at: z.string().nullable().optional(),
  environment_id: z.string().nullable().optional(),
  environment_name: z.string().nullable().optional(),
  workspace_id: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  engine_id: z.string().nullable().optional(),
  engine_name: z.string().nullable().optional(),
  engine_status: z.string().nullable().optional(),
  engine_version: z.string().nullable().optional(),
  engine_last_seen_at: z.string().nullable().optional(),
  job_id: z.string().nullable().optional(),
  job_name: z.string().nullable().optional(),
  job_display_name: z.string().nullable().optional(),
  job_run_id: z.string().nullable().optional(),
  plan_id: z.string().nullable().optional(),
  plan_name: z.string().nullable().optional(),
  plan_display_name: z.string().nullable().optional(),
  plan_run_id: z.string().nullable().optional(),
});
export type RuntimeFailureEstateContext = z.infer<
  typeof RuntimeFailureEstateContextSchema
>;

export const RuntimeExecutionSummaryInsightSchema = z.object({
  project_id: z.string(),
  execution_id: z.string(),
  job_name: z.string(),
  warning_count: z.number(),
  error_count: z.number(),
  first_warning_at: z.string().nullable().optional(),
  first_error_at: z.string().nullable().optional(),
  final_error_at: z.string().nullable().optional(),
  failed_component: z.string().nullable().optional(),
  primary_error_code: z.string().nullable().optional(),
  primary_exception_class: z.string().nullable().optional(),
  summary_text: z.string().nullable().optional(),
  // The log's own diagnostic sentence, credential-redacted. Absent until the
  // backend persists it (migration 081); optional so consumers degrade cleanly.
  causal_message: z.string().nullable().optional(),
  evidence_refs: z.array(z.number()).nullable().optional(),
  state: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  ended_at: z.string().nullable().optional(),
  duration_ms: z.number().nullable().optional(),
  project_name: z.string().nullable().optional(),
  incident_id: z.string().nullable().optional(),
  has_incident: z.boolean(),
  estate_context: RuntimeFailureEstateContextSchema,
});
export type RuntimeExecutionSummaryInsight = z.infer<
  typeof RuntimeExecutionSummaryInsightSchema
>;

export const ExecutionSummaryResponseSchema = z.object({
  summary: RuntimeExecutionSummaryInsightSchema,
});
export type ExecutionSummaryResponse = z.infer<typeof ExecutionSummaryResponseSchema>;

export const RuntimeTimelineEventSchema = z.object({
  log_event_id: z.number(),
  timestamp: z.string(),
  severity: z.string(),
  signal: z.string(),
  component_name: z.string().nullable().optional(),
  error_code: z.string().nullable().optional(),
  exception_class: z.string().nullable().optional(),
  retry_attempt: z.number().nullable().optional(),
  retry_max: z.number().nullable().optional(),
  confidence: z.string().nullable().optional(),
  message_family: z.string().nullable().optional(),
});
export type RuntimeTimelineEvent = z.infer<typeof RuntimeTimelineEventSchema>;

export const TimelineResponseSchema = z.object({
  execution_id: z.string(),
  events: z.array(RuntimeTimelineEventSchema),
  total: z.number(),
});
export type TimelineResponse = z.infer<typeof TimelineResponseSchema>;

export const RuntimeIncidentInsightSchema = z.object({
  project_id: z.string(),
  incident_id: z.string(),
  execution_id: z.string(),
  signature_hash: z.string().nullable().optional(),
  symptom_summary: z.string().nullable().optional(),
  root_cause_candidate: z.string().nullable().optional(),
  confidence: z.string().nullable().optional(),
  // "deterministic" (rule engine) or "llm_fallback". Zod strips unknown keys, so
  // omitting this silently dropped the field the UI needs to distinguish an
  // inferred cause from a derived one.
  cause_origin: z.string().nullable().optional(),
  pattern_id: z.string().nullable().optional(),
  // See RuntimeExecutionSummaryInsightSchema.causal_message.
  causal_message: z.string().nullable().optional(),
  first_bad_at: z.string().nullable().optional(),
  last_healthy_at: z.string().nullable().optional(),
  next_checks: z.array(z.string()),
  job_name: z.string().nullable().optional(),
  state: z.string().nullable().optional(),
  estate_context: RuntimeFailureEstateContextSchema,
});
export type RuntimeIncidentInsight = z.infer<typeof RuntimeIncidentInsightSchema>;

export const IncidentListResponseSchema = z.object({
  incidents: z.array(RuntimeIncidentInsightSchema),
  total: z.number(),
  offset: z.number().optional().default(0),
  limit: z.number().optional().default(0),
});
export type IncidentListResponse = z.infer<typeof IncidentListResponseSchema>;

export const IncidentDetailResponseSchema = z.object({
  incident: RuntimeIncidentInsightSchema,
});
export type IncidentDetailResponse = z.infer<typeof IncidentDetailResponseSchema>;

export const RuntimeIncidentEvidenceSchema = z.object({
  log_event_id: z.number(),
  evidence_role: z.string().nullable().optional(),
  excerpt: z.string().nullable().optional(),
  timestamp: z.string(),
  severity: z.string(),
});
export type RuntimeIncidentEvidence = z.infer<typeof RuntimeIncidentEvidenceSchema>;

export const IncidentEvidenceResponseSchema = z.object({
  incident_id: z.string(),
  evidence: z.array(RuntimeIncidentEvidenceSchema),
  total: z.number(),
});
export type IncidentEvidenceResponse = z.infer<typeof IncidentEvidenceResponseSchema>;

// --- Similar failures ---

export const RuntimeSimilarIncidentSchema = z.object({
  incident_id: z.string(),
  execution_id: z.string(),
  job_name: z.string().nullable().optional(),
  state: z.string().nullable().optional(),
  first_bad_at: z.string().nullable().optional(),
  symptom_summary: z.string().nullable().optional(),
  root_cause_candidate: z.string().nullable().optional(),
  confidence: z.string().nullable().optional(),
});
export type RuntimeSimilarIncident = z.infer<typeof RuntimeSimilarIncidentSchema>;

export const RuntimeAlertReadyMetadataSchema = z.object({
  signature_hash: z.string().nullable().optional(),
  signature_summary: z.string().nullable().optional(),
  occurrence_count: z.number(),
  similar_count: z.number(),
  first_seen_at: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
  is_recurring: z.boolean(),
  alert_fingerprint: z.string(),
  dedupe_key: z.string(),
  alert_title: z.string(),
  alert_reason: z.string(),
});
export type RuntimeAlertReadyMetadata = z.infer<typeof RuntimeAlertReadyMetadataSchema>;

export const IncidentSimilarResponseSchema = z.object({
  incident_id: z.string(),
  execution_id: z.string(),
  recurrence: RuntimeAlertReadyMetadataSchema,
  similar_incidents: z.array(RuntimeSimilarIncidentSchema),
});
export type IncidentSimilarResponse = z.infer<typeof IncidentSimilarResponseSchema>;

// --- Aggregate rollups ---
// SQL-computed rollups over run facts and component metrics. Unlike the entity
// lists above these responses carry no offset/limit echo, so they are typed
// exactly as the backend returns them.

export const RuntimeTrendBucketSchema = z.enum(["hour", "day", "week"]);
export type RuntimeTrendBucket = z.infer<typeof RuntimeTrendBucketSchema>;

export const RuntimeOutcomeTrendPointSchema = z.object({
  bucket_start: z.string(),
  total_runs: z.number(),
  success_runs: z.number(),
  failed_runs: z.number(),
  running_runs: z.number(),
  unknown_runs: z.number(),
  failure_rate: z.number(),
  p50_duration_ms: z.number().nullable().optional(),
  p95_duration_ms: z.number().nullable().optional(),
  max_duration_ms: z.number().nullable().optional(),
  // A misfire never started, a timeout/termination stopped mid-flight -- none
  // of the three were ever "unknown", so they get their own counters instead
  // of continuing to hide inside it. Defaulted to 0 so an older backend
  // response (pre-widening) still parses.
  misfired_runs: z.number().optional().default(0),
  terminated_runs: z.number().optional().default(0),
  timed_out_runs: z.number().optional().default(0),
});
export type RuntimeOutcomeTrendPoint = z.infer<typeof RuntimeOutcomeTrendPointSchema>;

export const RuntimeTrendCoverageSchema = z.object({
  observed_from: z.string().nullable(),
  observed_until: z.string().nullable(),
  completeness: z.literal("unknown"),
});
export type RuntimeTrendCoverage = z.infer<typeof RuntimeTrendCoverageSchema>;

export const RuntimeTrendResponseSchema = z.object({
  bucket: RuntimeTrendBucketSchema,
  points: z.array(RuntimeOutcomeTrendPointSchema),
  total: z.number(),
  coverage: RuntimeTrendCoverageSchema,
});
export type RuntimeTrendResponse = z.infer<typeof RuntimeTrendResponseSchema>;

export const RuntimeJobReliabilitySchema = z.object({
  project_id: z.string(),
  source_key: z.string(),
  job_name: z.string(),
  total_runs: z.number(),
  success_runs: z.number(),
  failed_runs: z.number(),
  // Runs with no ended_at: never-completed work the run list reports as success.
  unfinished_runs: z.number(),
  failure_rate: z.number(),
  current_failure_streak: z.number(),
  first_run_at: z.string().nullable().optional(),
  last_run_at: z.string().nullable().optional(),
  latest_state: RuntimeExecutionStateSchema.nullable().optional(),
  // Current-state signal: true only when the latest run has no end time and
  // is not normally running. Historical unfinished work is kept separately.
  latest_unfinished: z.boolean().default(false),
  p50_duration_ms: z.number().nullable().optional(),
  p95_duration_ms: z.number().nullable().optional(),
  max_duration_ms: z.number().nullable().optional(),
  // See RuntimeOutcomeTrendPointSchema: same three outcomes, same reason for
  // defaulting to 0 rather than requiring the backend to have widened yet.
  misfired_runs: z.number().optional().default(0),
  terminated_runs: z.number().optional().default(0),
  timed_out_runs: z.number().optional().default(0),
});
export type RuntimeJobReliability = z.infer<typeof RuntimeJobReliabilitySchema>;

export const RuntimeJobReliabilityResponseSchema = z.object({
  jobs: z.array(RuntimeJobReliabilitySchema),
  total: z.number(),
});
export type RuntimeJobReliabilityResponse = z.infer<
  typeof RuntimeJobReliabilityResponseSchema
>;

/**
 * A bounded, job-level explanation of recent operational behaviour.
 *
 * The backend derives this from execution summaries and incidents; it never
 * includes raw log lines. Nullable diagnosis fields are intentional: a failure
 * without enough evidence must remain unexplained instead of becoming a guess.
 */
export const RuntimeJobInsightSchema = z.object({
  project_id: z.string(),
  source_key: z.string(),
  job_name: z.string(),
  total_runs: z.number(),
  success_runs: z.number(),
  failed_runs: z.number(),
  unfinished_runs: z.number(),
  failure_rate: z.number(),
  first_run_at: z.string().nullable().optional(),
  last_run_at: z.string().nullable().optional(),
  failures_with_logs: z.number(),
  diagnosed_failures: z.number(),
  capture_ceiling_reached: z.number(),
  evidence_completeness: z.enum([
    "no_failures",
    "complete",
    "partial",
    "logs_only",
    "missing",
  ]),
  pattern_id: z.string().nullable().optional(),
  confidence: z.string().nullable().optional(),
  occurrences: z.number(),
  most_recent_at: z.string().nullable().optional(),
  failed_component: z.string().nullable().optional(),
  termination_component: z.string().nullable().optional(),
  causal_message: z.string().nullable().optional(),
  root_cause_candidate: z.string().nullable().optional(),
  cause_origin: z.string().nullable().optional(),
  next_checks: z.array(z.string()),
});
export type RuntimeJobInsight = z.infer<typeof RuntimeJobInsightSchema>;

export const RuntimeJobInsightResponseSchema = z.object({
  insights: z.array(RuntimeJobInsightSchema),
  total: z.number(),
});
export type RuntimeJobInsightResponse = z.infer<
  typeof RuntimeJobInsightResponseSchema
>;

export const RuntimeComponentHotspotSchema = z.object({
  project_id: z.string(),
  source_key: z.string(),
  component_key: z.string(),
  component_type: z.string().nullable().optional(),
  source_connector_type: z.string().nullable().optional(),
  target_connector_type: z.string().nullable().optional(),
  executions: z.number(),
  rows_total: z.number().nullable().optional(),
  rows_rejected: z.number().nullable().optional(),
  reject_ratio: z.number().nullable().optional(),
  avg_duration_ms: z.number().nullable().optional(),
  p95_duration_ms: z.number().nullable().optional(),
  max_duration_ms: z.number().nullable().optional(),
  last_measured_at: z.string().nullable().optional(),
});
export type RuntimeComponentHotspot = z.infer<typeof RuntimeComponentHotspotSchema>;

export const RuntimeComponentHotspotResponseSchema = z.object({
  components: z.array(RuntimeComponentHotspotSchema),
  total: z.number(),
});
export type RuntimeComponentHotspotResponse = z.infer<
  typeof RuntimeComponentHotspotResponseSchema
>;

export const RuntimeJobThroughputSchema = z.object({
  project_id: z.string(),
  source_key: z.string(),
  job_name: z.string(),
  executions_with_metrics: z.number(),
  component_count: z.number(),
  rows_total: z.number().nullable().optional(),
  rows_rejected: z.number().nullable().optional(),
  reject_ratio: z.number().nullable().optional(),
  last_measured_at: z.string().nullable().optional(),
});
export type RuntimeJobThroughput = z.infer<typeof RuntimeJobThroughputSchema>;

export const RuntimeJobThroughputResponseSchema = z.object({
  jobs: z.array(RuntimeJobThroughputSchema),
  total: z.number(),
});
export type RuntimeJobThroughputResponse = z.infer<
  typeof RuntimeJobThroughputResponseSchema
>;

export const RuntimeErrorSignatureSchema = z.object({
  project_id: z.string(),
  signature_hash: z.string(),
  error_code: z.string().nullable().optional(),
  exception_class: z.string().nullable().optional(),
  component_name: z.string().nullable().optional(),
  summary_text: z.string().nullable().optional(),
  // See RuntimeExecutionSummaryInsightSchema.causal_message.
  causal_message: z.string().nullable().optional(),
  occurrence_count: z.number(),
  first_seen_at: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
});
export type RuntimeErrorSignature = z.infer<typeof RuntimeErrorSignatureSchema>;

export const RuntimeErrorSignatureResponseSchema = z.object({
  signatures: z.array(RuntimeErrorSignatureSchema),
  total: z.number(),
});
export type RuntimeErrorSignatureResponse = z.infer<
  typeof RuntimeErrorSignatureResponseSchema
>;

// --- Runtime source attachments (which projects may read one mirrored tenant) ---

/**
 * One project ATTACHED to a runtime source.
 *
 * A vendor tenant is registered once (`physical_identity` is globally unique)
 * and mirrored once, however many projects attach to it. There is no owner:
 * every project that may read the source has one of these rows, including the
 * project named at registration. Mirrors `RuntimeSourceReadGrant` in
 * `backend/src/runtime/models.py`.
 *
 * Every field the backend returns is declared here on purpose. A field left out
 * of a proxy schema is stripped by Zod and is then permanently `undefined` in
 * the browser with no error anywhere -- and `exposes_project_ids` is the one
 * field whose entire job is to warn the operator, so losing it silently is the
 * worst outcome this contract can produce.
 */
export const RuntimeSourceReadGrantSchema = z.object({
  id: z.number(),
  source_id: z.number(),
  /** The attached project. */
  project_id: z.string(),
  /**
   * The source's estate key -- its internal row stamp, not a project. Renamed
   * from `source_project_id` when sources stopped having an owning project.
   */
  source_estate_key: z.string(),
  source_label: z.string(),
  /** `null` means every mirrored environment; `[]` is a deliberate "nothing". */
  environment_ids: z.array(z.string()).nullable(),
  window_days: z.number().nullable(),
  /**
   * Every row stamp this attachment exposes: the source's own estate key plus
   * any project its workspace map re-points rows to. More than one entry means
   * the attachment reaches further than the source and the project named on it.
   */
  exposes_project_ids: z.array(z.string()),
  created_at: z.string(),
  created_by: z.string().nullable(),
});
export type RuntimeSourceReadGrant = z.infer<typeof RuntimeSourceReadGrantSchema>;

export const RuntimeSourceReadGrantListSchema = z.object({
  grants: z.array(RuntimeSourceReadGrantSchema),
  total: z.number(),
});
export type RuntimeSourceReadGrantList = z.infer<typeof RuntimeSourceReadGrantListSchema>;

export const RuntimeSourceReadGrantResponseSchema = z.object({
  grant: RuntimeSourceReadGrantSchema,
});
export type RuntimeSourceReadGrantResponse = z.infer<
  typeof RuntimeSourceReadGrantResponseSchema
>;
