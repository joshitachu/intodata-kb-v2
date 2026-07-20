"use client";

import { useQuery } from "@tanstack/react-query";
import {
  RuntimeJobHealthResponseSchema,
  type RuntimeJobHealthResponse,
} from "@/types";

// Live runtime-health poll for the topology overlay (S3). Mirrors the
// conventions in hooks/useRuntimeLogs.ts (Zod-validated, project-scoped) but
// adds a refetchInterval so the overlay stays "sync-cadence fresh" without a
// manual reload. The endpoint is RBAC-scoped server-side; `project_id` is a
// filter within the caller's scope, never an authorization source.

const REFETCH_MS = 45 * 1000;

async function fetchJobHealth(projectId: string): Promise<RuntimeJobHealthResponse> {
  const params = new URLSearchParams({ project_id: projectId, limit: "500" });
  const res = await fetch(`/api/runtime/job-health?${params.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch runtime job health (${res.status})`);
  }
  return RuntimeJobHealthResponseSchema.parse(await res.json());
}

/**
 * Poll `/runtime/job-health` for one project. Idle until a project is selected
 * and the overlay is enabled, so no request fires when the overlay is off.
 */
export function useJobHealth(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["runtime", "topology-job-health", projectId],
    queryFn: () => fetchJobHealth(projectId),
    enabled: Boolean(projectId) && enabled,
    refetchInterval: enabled ? REFETCH_MS : false,
    staleTime: REFETCH_MS,
  });
}
