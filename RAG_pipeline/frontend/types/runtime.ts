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
