import { z } from "zod";

// Runtime logs API (EP-005 slice 2 backend contract). Datetimes arrive as ISO-8601
// strings in JSON, mirroring RuntimeStatusResponse.checked_at.

export const RuntimeExecutionStateSchema = z.enum([
  "success",
  "failed",
  "running",
  "unknown",
]);
export type RuntimeExecutionState = z.infer<typeof RuntimeExecutionStateSchema>;

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
  started_at: z.string(),
  ended_at: z.string().nullable().optional(),
  duration_ms: z.number().nullable().optional(),
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
});
export type RuntimeLogEvent = z.infer<typeof RuntimeLogEventSchema>;

export const ExecutionListResponseSchema = z.object({
  executions: z.array(RuntimeExecutionSchema),
  total: z.number(),
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

// --- Smart-log insight schemas (EP-006 slice 2 backend contract) ---

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
  evidence_refs: z.array(z.number()).nullable().optional(),
  state: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  ended_at: z.string().nullable().optional(),
  duration_ms: z.number().nullable().optional(),
  project_name: z.string().nullable().optional(),
  incident_id: z.string().nullable().optional(),
  has_incident: z.boolean(),
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
  message: z.string(),
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
  first_bad_at: z.string().nullable().optional(),
  last_healthy_at: z.string().nullable().optional(),
  next_checks: z.array(z.string()),
  job_name: z.string().nullable().optional(),
  state: z.string().nullable().optional(),
});
export type RuntimeIncidentInsight = z.infer<typeof RuntimeIncidentInsightSchema>;

export const IncidentListResponseSchema = z.object({
  incidents: z.array(RuntimeIncidentInsightSchema),
  total: z.number(),
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
