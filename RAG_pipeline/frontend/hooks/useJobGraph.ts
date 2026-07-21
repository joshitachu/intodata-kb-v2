"use client";

import { useQuery } from "@tanstack/react-query";
import { JobGraphResponseSchema, type JobGraphResponse } from "@/types/jobGraph";

async function fetchJobGraph(
  projectId: string,
  jobName: string,
): Promise<JobGraphResponse> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobName)}/graph`,
  );
  if (!res.ok) {
    const text = await res.text();
    let message = `Failed to fetch job graph (${res.status})`;
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
  return JobGraphResponseSchema.parse(await res.json());
}

export function useJobGraph(projectId: string, jobName: string | null) {
  return useQuery({
    queryKey: ["job-graph", projectId, jobName],
    queryFn: () => fetchJobGraph(projectId, jobName as string),
    enabled: Boolean(projectId && jobName),
    staleTime: 5 * 60 * 1000,
  });
}
