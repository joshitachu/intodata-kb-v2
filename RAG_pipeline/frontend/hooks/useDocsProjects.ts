"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
import type { DocsProjectsResponse } from "@/types";

export function useDocsProjects() {
  return useQuery({
    queryKey: ["docs-projects"],
    // `fetchJson` refreshes the session and replays once on a 401, so the
    // stale-access-token window on a cold load resolves itself instead of
    // leaving the selector claiming the account has no projects.
    queryFn: () =>
      fetchJson<DocsProjectsResponse>("/api/docs/projects", {
        fallbackError: "Failed to fetch projects",
      }),
  });
}
