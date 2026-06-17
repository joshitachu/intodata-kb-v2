"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ExecutionDetailResponse,
  ExecutionDetailResponseSchema,
  ExecutionListResponse,
  ExecutionListResponseSchema,
  ExecutionSummaryResponse,
  ExecutionSummaryResponseSchema,
  FailureListResponse,
  FailureListResponseSchema,
  IncidentDetailResponse,
  IncidentDetailResponseSchema,
  IncidentEvidenceResponse,
  IncidentEvidenceResponseSchema,
  IncidentListResponse,
  IncidentListResponseSchema,
  IncidentSimilarResponse,
  IncidentSimilarResponseSchema,
  LogEventListResponse,
  LogEventListResponseSchema,
  RuntimeExecutionState,
  RuntimeLogSeverity,
  TimelineResponse,
  TimelineResponseSchema,
} from "@/types";

export interface ExecutionFilters {
  projectId?: string;
  jobName?: string;
  state?: RuntimeExecutionState;
  limit?: number;
}

export interface FailureFilters {
  projectId?: string;
  limit?: number;
}

export interface LogFilters {
  minSeverity?: RuntimeLogSeverity;
  limit?: number;
}

/** Build a query string from defined, non-empty filter values. */
function buildQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

const STALE_MS = 15 * 1000;

async function fetchExecutions(
  filters: ExecutionFilters,
): Promise<ExecutionListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    job_name: filters.jobName,
    state: filters.state,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/executions${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch executions");
  }
  return ExecutionListResponseSchema.parse(await res.json());
}

export function useRuntimeExecutions(filters: ExecutionFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "executions", filters],
    queryFn: () => fetchExecutions(filters),
    staleTime: STALE_MS,
  });
}

async function fetchExecution(
  executionId: string,
): Promise<ExecutionDetailResponse> {
  const res = await fetch(
    `/api/runtime/executions/${encodeURIComponent(executionId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error("Failed to fetch execution");
  }
  return ExecutionDetailResponseSchema.parse(await res.json());
}

export function useRuntimeExecution(executionId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["runtime", "execution", executionId],
    queryFn: () => fetchExecution(executionId as string),
    enabled: enabled && !!executionId,
    staleTime: STALE_MS,
  });
}

async function fetchExecutionLogs(
  executionId: string,
  filters: LogFilters,
): Promise<LogEventListResponse> {
  const query = buildQuery({
    min_severity: filters.minSeverity,
    limit: filters.limit,
  });
  const res = await fetch(
    `/api/runtime/executions/${encodeURIComponent(executionId)}/logs${query}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error("Failed to fetch execution logs");
  }
  return LogEventListResponseSchema.parse(await res.json());
}

export function useRuntimeExecutionLogs(
  executionId: string | null,
  filters: LogFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "logs", executionId, filters],
    queryFn: () => fetchExecutionLogs(executionId as string, filters),
    enabled: enabled && !!executionId,
    staleTime: STALE_MS,
  });
}

async function fetchFailures(filters: FailureFilters): Promise<FailureListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/failures${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch failures");
  }
  return FailureListResponseSchema.parse(await res.json());
}

export function useRuntimeFailures(filters: FailureFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "failures", filters],
    queryFn: () => fetchFailures(filters),
    staleTime: STALE_MS,
  });
}

// --- Smart-log insight hooks (EP-006 slice 4) ---

export interface IncidentFilters {
  projectId?: string;
  since?: string;
  until?: string;
  limit?: number;
}

export interface TimelineFilters {
  minSeverity?: RuntimeLogSeverity;
  limit?: number;
}

export interface EvidenceFilters {
  limit?: number;
}

async function fetchExecutionSummary(
  executionId: string,
): Promise<ExecutionSummaryResponse> {
  const res = await fetch(
    `/api/runtime/executions/${encodeURIComponent(executionId)}/summary`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch execution summary: ${res.status}`);
  }
  return ExecutionSummaryResponseSchema.parse(await res.json());
}

export function useRuntimeExecutionSummary(
  executionId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "summary", executionId],
    queryFn: () => fetchExecutionSummary(executionId as string),
    enabled: enabled && !!executionId,
    staleTime: STALE_MS,
  });
}

async function fetchExecutionTimeline(
  executionId: string,
  filters: TimelineFilters,
): Promise<TimelineResponse> {
  const query = buildQuery({
    min_severity: filters.minSeverity,
    limit: filters.limit,
  });
  const res = await fetch(
    `/api/runtime/executions/${encodeURIComponent(executionId)}/timeline${query}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch execution timeline: ${res.status}`);
  }
  return TimelineResponseSchema.parse(await res.json());
}

export function useRuntimeExecutionTimeline(
  executionId: string | null,
  filters: TimelineFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "timeline", executionId, filters],
    queryFn: () => fetchExecutionTimeline(executionId as string, filters),
    enabled: enabled && !!executionId,
    staleTime: STALE_MS,
  });
}

async function fetchIncidents(filters: IncidentFilters): Promise<IncidentListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/incidents${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch incidents");
  }
  return IncidentListResponseSchema.parse(await res.json());
}

export function useRuntimeIncidents(filters: IncidentFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "incidents", filters],
    queryFn: () => fetchIncidents(filters),
    staleTime: STALE_MS,
  });
}

async function fetchIncident(incidentId: string): Promise<IncidentDetailResponse> {
  const res = await fetch(
    `/api/runtime/incidents/${encodeURIComponent(incidentId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch incident: ${res.status}`);
  }
  return IncidentDetailResponseSchema.parse(await res.json());
}

export function useRuntimeIncident(incidentId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["runtime", "incident", incidentId],
    queryFn: () => fetchIncident(incidentId as string),
    enabled: enabled && !!incidentId,
    staleTime: STALE_MS,
  });
}

async function fetchIncidentEvidence(
  incidentId: string,
  filters: EvidenceFilters,
): Promise<IncidentEvidenceResponse> {
  const query = buildQuery({ limit: filters.limit });
  const res = await fetch(
    `/api/runtime/incidents/${encodeURIComponent(incidentId)}/evidence${query}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch incident evidence: ${res.status}`);
  }
  return IncidentEvidenceResponseSchema.parse(await res.json());
}

export function useRuntimeIncidentEvidence(
  incidentId: string | null,
  filters: EvidenceFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "evidence", incidentId, filters],
    queryFn: () => fetchIncidentEvidence(incidentId as string, filters),
    enabled: enabled && !!incidentId,
    staleTime: STALE_MS,
  });
}

// --- Similar failures (EP-006 slice 5) ---

export interface SimilarFailuresFilters {
  limit?: number;
}

async function fetchSimilarFailures(
  incidentId: string,
  filters: SimilarFailuresFilters,
): Promise<IncidentSimilarResponse> {
  const query = buildQuery({ limit: filters.limit });
  const res = await fetch(
    `/api/runtime/incidents/${encodeURIComponent(incidentId)}/similar${query}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch similar failures: ${res.status}`);
  }
  return IncidentSimilarResponseSchema.parse(await res.json());
}

export function useRuntimeSimilarFailures(
  incidentId: string | null,
  filters: SimilarFailuresFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "similar", incidentId, filters],
    queryFn: () => fetchSimilarFailures(incidentId as string, filters),
    enabled: enabled && !!incidentId,
    staleTime: STALE_MS,
  });
}
