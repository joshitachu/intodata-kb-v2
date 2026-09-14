"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
import type { DeepDiveProjectsResponse } from "@/types";

export function useDeepDiveProjects() {
  return useQuery({
    queryKey: ["deep-dive-projects"],
    queryFn: () =>
      fetchJson<DeepDiveProjectsResponse>("/api/deep-dive/projects", {
        fallbackError: "Failed to fetch projects",
      }),
  });
}
