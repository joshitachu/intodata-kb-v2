"use client";

import { useQuery } from "@tanstack/react-query";
import { ZodError } from "zod";
import { authedFetch, isAuthError } from "@/lib/authedFetch";
import { ProjectLandscapeResponseSchema, type ProjectLandscapeResponse } from "@/types/landscape";

/** A documented absence (pre-landscape scan), distinct from access and schema errors. */
export class LandscapeUnavailableError extends Error {
  readonly status = 404;
  constructor() {
    super("This project needs a rescan for the organization landscape");
    this.name = "LandscapeUnavailableError";
  }
}

async function fetchProjectLandscape(projectId: string): Promise<ProjectLandscapeResponse> {
  // `authedFetch`, not `fetchJson`: a 404 here is a documented absence and has
  // to be classified before any generic error path can turn it into a failure.
  const response = await authedFetch(`/api/projects/${encodeURIComponent(projectId)}/landscape`);
  if (response.status === 404) throw new LandscapeUnavailableError();
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: unknown } | null;
    throw new Error(typeof body?.error === "string" ? body.error : `Failed to fetch landscape (${response.status})`);
  }
  return ProjectLandscapeResponseSchema.parse(await response.json());
}

export function useProjectLandscape(projectId: string) {
  return useQuery({
    queryKey: ["project-landscape", projectId],
    queryFn: () => fetchProjectLandscape(projectId),
    enabled: Boolean(projectId),
    staleTime: 5 * 60 * 1000,
    // A 404 and a contract violation are both deterministic outcomes. Neither
    // benefits from retrying and neither may be disguised as a legacy scan.
    // An auth failure is equally pointless to repeat: `authedFetch` has already
    // rotated the token and replayed once, so a second attempt would only
    // re-send the same rejected credential.
    retry: (attempt, error) =>
      !isAuthError(error) &&
      !(error instanceof LandscapeUnavailableError) && !(error instanceof ZodError) && attempt < 2,
  });
}
