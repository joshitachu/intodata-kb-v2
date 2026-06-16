"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ExecutionDetailResponse,
  ExecutionDetailResponseSchema,
  ExecutionListResponse,
  ExecutionListResponseSchema,
  FailureListResponse,
  FailureListResponseSchema,
  LogEventListResponse,
  LogEventListResponseSchema,
  RuntimeExecutionState,
  RuntimeLogSeverity,
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
