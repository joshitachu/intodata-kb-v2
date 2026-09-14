"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ComponentGraphResponseSchema,
  ComponentGraphFallbackSchema,
  type ComponentGraphResponse,
} from "@/types/componentGraph";
import { authedFetch } from "@/lib/authedFetch";

// Thrown when the API's documented 404 { fallback: "inspector" } contract
// fires (any component without a level-3 graph - all non-tMap types today).
// Distinct from a generic fetch Error so callers can react to it directly
// (open the inspector) instead of rendering a hard error - "no dead ends"
// is the S2 acceptance criterion.
export class ComponentGraphFallbackError extends Error {
  readonly fallback = "inspector" as const;
  constructor() {
    super("Component has no level-3 graph; falling back to inspector");
    this.name = "ComponentGraphFallbackError";
  }
}

async function fetchComponentGraph(
  projectId: string,
  jobName: string,
  componentName: string,
): Promise<ComponentGraphResponse> {
  const res = await authedFetch(
    `/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobName)}/components/${encodeURIComponent(componentName)}/graph`,
  );

  if (!res.ok) {
    const text = await res.text();
    let parsed: unknown = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      // parsed stays null; falls through to the generic error below
    }

    if (res.status === 404) {
      const fallback = ComponentGraphFallbackSchema.safeParse(parsed);
      if (fallback.success) {
        throw new ComponentGraphFallbackError();
      }
    }

    let message = `Failed to fetch component graph (${res.status})`;
    if (
      parsed !== null &&
      typeof parsed === "object" &&
      "error" in parsed &&
      typeof (parsed as { error: unknown }).error === "string"
    ) {
      message = (parsed as { error: string }).error;
    }
    throw new Error(message);
  }

  return ComponentGraphResponseSchema.parse(await res.json());
}

export function useComponentGraph(
  projectId: string,
  jobName: string | null,
  componentName: string | null,
) {
  return useQuery({
    queryKey: ["component-graph", projectId, jobName, componentName],
    queryFn: () =>
      fetchComponentGraph(projectId, jobName as string, componentName as string),
    enabled: Boolean(projectId && jobName && componentName),
    staleTime: 5 * 60 * 1000,
    // The fallback contract is a documented outcome, not a transient
    // failure - retrying it would just re-fetch the same 404 three times
    // before the UI can react.
    retry: false,
  });
}
