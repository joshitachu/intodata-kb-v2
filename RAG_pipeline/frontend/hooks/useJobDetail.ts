"use client";

import { useQuery } from "@tanstack/react-query";
import { JobDetailResponseSchema, type JobDetailResponse } from "@/types/jobDetail";

async function fetchJobDetail(
  projectId: string,
  jobName: string,
): Promise<JobDetailResponse> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobName)}/detail`,
  );
  if (!res.ok) {
    const text = await res.text();
    let message = `Failed to fetch job detail (${res.status})`;
    try {
      const parsed: unknown = JSON.parse(text);
      if (
        parsed !== null &&
        typeof parsed === "object" &&
        "error" in parsed &&
        typeof (parsed as { error: unknown }).error === "string"
      ) {
        message = (parsed as { error: string }).error;
      }
    } catch {
      // use default message
    }
    throw new Error(message);
  }
  return JobDetailResponseSchema.parse(await res.json());
}

// Shared job-detail fetcher: originally inlined in JobDetailPanel, extracted
// so ComponentInspector can look up the same component's raw scan fields
// (params/code/connectors) without a second fetch implementation.
export function useJobDetail(projectId: string, jobName: string | null) {
  return useQuery({
    queryKey: ["job-detail", projectId, jobName],
    queryFn: () => fetchJobDetail(projectId, jobName as string),
    enabled: Boolean(projectId && jobName),
    staleTime: 5 * 60 * 1000,
  });
}
