"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchJson, retryTransientOnly } from "@/lib/authedFetch";
import type {
  DocumentRoutingResponse,
  ProjectWorkspaceResponse,
} from "@/types/guidance";

/**
 * Reads for the agent's worktree and for the routing table that fills it.
 *
 * Kept beside the components that consume them rather than in
 * `hooks/useGuidance.ts`, which owns the *editable* stack. These two are pure
 * reads of derived state: the workspace is assembled from the stack plus the
 * project's material, and the routing table is a constant the backend owns.
 *
 * The cache key is the project id and nothing else. File contents run to tens of
 * kilobytes and putting them anywhere near a key would turn every cache lookup
 * into a full-tree comparison; the payload belongs in the value, not the key.
 */

export const workspaceKeys = {
  workspace: (projectId: string) => ["guidance", "workspace", projectId] as const,
  documentRouting: ["guidance", "document-routing"] as const,
};

export function useProjectWorkspace(projectId: string) {
  return useQuery({
    queryKey: workspaceKeys.workspace(projectId),
    queryFn: () =>
      fetchJson<ProjectWorkspaceResponse>(
        `/api/guidance/projects/${encodeURIComponent(projectId)}/workspace`,
        { fallbackError: "Failed to load the project workspace" },
      ),
    enabled: projectId.length > 0,
    retry: retryTransientOnly,
  });
}

/**
 * The role-to-destination table.
 *
 * It changes only when the product's routing rules change, so it is held for
 * the session rather than refetched behind every keystroke in the upload form.
 */
export function useDocumentRouting() {
  return useQuery({
    queryKey: workspaceKeys.documentRouting,
    queryFn: () =>
      fetchJson<DocumentRoutingResponse>("/api/guidance/document-routing", {
        fallbackError: "Failed to load the document routing table",
      }),
    staleTime: 30 * 60 * 1000,
    retry: retryTransientOnly,
  });
}
