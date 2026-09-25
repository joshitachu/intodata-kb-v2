"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
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

/**
 * Which deployed things to ask for.
 *
 * `"job"` is what the L3 technical job graph wants: its nodes ARE scanner jobs,
 * and pulling routes and data services into that join adds rows no node can
 * ever bind to. `"all"` is what the business/architecture layer wants: a
 * business stream is implemented by whatever designs deliver it, and on
 * Pluripharm roughly a third of those are routes.
 *
 * A narrow kind is sent as an explicit `executable_kind` filter; `"all"` omits
 * the parameter, because omission is exactly what the endpoint defines as
 * "every kind". The L3 caller passes `"job"` EXPLICITLY for that reason:
 * omission used to mean "jobs only" server-side and now means "everything", so
 * leaving it off would silently change what the job graph renders the day the
 * backend ships.
 */
export type JobHealthKind = "job" | "route" | "data_service" | "all";

async function fetchJobHealth(
  projectId: string,
  kind: JobHealthKind,
): Promise<RuntimeJobHealthResponse> {
  const params = new URLSearchParams({ project_id: projectId, limit: "500" });
  if (kind !== "all") params.set("executable_kind", kind);
  return RuntimeJobHealthResponseSchema.parse(
    await fetchJson<unknown>(`/api/runtime/job-health?${params.toString()}`, {
      cache: "no-store",
      fallbackError: "Failed to fetch runtime job health",
    }),
  );
}

/**
 * Poll `/runtime/job-health` for one project. Idle until a project is selected
 * and the overlay is enabled, so no request fires when the overlay is off.
 */
export function useJobHealth(projectId: string, enabled: boolean, kind: JobHealthKind = "job") {
  return useQuery({
    queryKey: ["runtime", "topology-job-health", projectId, kind],
    queryFn: () => fetchJobHealth(projectId, kind),
    enabled: Boolean(projectId) && enabled,
    refetchInterval: enabled ? REFETCH_MS : false,
    staleTime: REFETCH_MS,
  });
}
