"use client";

import { useQuery } from "@tanstack/react-query";
import {
  LineageFieldResponseSchema,
  type FieldLineageQuery,
  type LineageFieldResponse,
} from "@/types/lineage";

// Raised when a project has no scan/lineage.json at all (never scanned
// since the S4 lineage pass landed) - distinct from a generic fetch error so
// the UI can show a specific "no lineage data for this project" message
// rather than a scary error banner, mirroring ComponentGraphFallbackError's
// role for the level-3 404 contract.
export class LineageDataUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LineageDataUnavailableError";
  }
}

async function fetchFieldLineage(
  projectId: string,
  query: FieldLineageQuery,
): Promise<LineageFieldResponse> {
  const params = new URLSearchParams({
    job: query.jobName,
    table: query.table,
    column: query.column,
  });
  if (query.component) params.set("component", query.component);
  if (query.direction) params.set("direction", query.direction);
  if (query.maxDepth !== undefined) params.set("max_depth", String(query.maxDepth));

  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/lineage/field?${params.toString()}`,
  );

  if (!res.ok) {
    const text = await res.text();
    let message = `Failed to fetch field lineage (${res.status})`;
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
    if (res.status === 404) {
      throw new LineageDataUnavailableError(message);
    }
    throw new Error(message);
  }

  return LineageFieldResponseSchema.parse(await res.json());
}

// Fetches the upstream/downstream path for one field, on demand - `query`
// is null until the user clicks a column handle (S5), so this hook is idle
// (no request in flight) the rest of the time.
export function useFieldLineage(projectId: string, query: FieldLineageQuery | null) {
  return useQuery({
    queryKey: [
      "field-lineage",
      projectId,
      query?.jobName,
      query?.table,
      query?.column,
      query?.component,
      query?.direction,
      query?.maxDepth,
    ],
    queryFn: () => fetchFieldLineage(projectId, query as FieldLineageQuery),
    enabled: Boolean(projectId && query),
    staleTime: 60 * 1000,
    // A 404 (no lineage data for the project) is a documented, stable
    // outcome, not a transient failure - don't hammer it three times.
    retry: false,
  });
}
