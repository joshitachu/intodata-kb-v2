"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiRequestError, fetchJson, retryTransientOnly } from "@/lib/authedFetch";
import type {
  GuidanceRule,
  GuidanceRuleCreateRequest,
  GuidanceRuleListResponse,
  GuidancePreviewResponse,
  GuidanceStatus,
  PersonPreferencesResponse,
  StylePreference,
} from "@/types/guidance";

/**
 * Data access for the layered guidance stack.
 *
 * Every write invalidates the whole `guidance` tree rather than a single list.
 * That is deliberate: adding a project rule changes the project list *and* the
 * rendered preview *and* the token consumption every budget meter reads off
 * that preview, so a narrower invalidation would leave a meter claiming room
 * that has already been spent.
 */

export const guidanceKeys = {
  all: ["guidance"] as const,
  org: (status: GuidanceStatus) => ["guidance", "org", status] as const,
  project: (projectId: string, status: GuidanceStatus) =>
    ["guidance", "project", projectId, status] as const,
  preferences: (projectId: string) => ["guidance", "preferences", projectId] as const,
  preview: (projectId: string) => ["guidance", "preview", projectId] as const,
};

/** The 409 the backend answers when a layer's token ceiling is reached. */
export const BUDGET_CONFLICT_STATUS = 409;

/**
 * The budget message, when a failure was a full layer and not a plain error.
 *
 * A 409 is an expected outcome an operator can act on -- the backend's message
 * names the layer, the arithmetic and the ceiling -- so it must not be rendered
 * as "something went wrong".
 */
export function budgetConflictMessage(error: unknown): string | null {
  if (error instanceof ApiRequestError && error.status === BUDGET_CONFLICT_STATUS) {
    return error.message;
  }
  return null;
}

/** Any failure, as a sentence. */
export function guidanceErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return fallback;
}

/** True when the caller was refused by the server rather than by the UI. */
export function isForbidden(error: unknown): boolean {
  return error instanceof ApiRequestError && error.status === 403;
}

// -- reads -------------------------------------------------------------------

export function useOrgGuidance(status: GuidanceStatus = "active") {
  return useQuery({
    queryKey: guidanceKeys.org(status),
    queryFn: () =>
      fetchJson<GuidanceRuleListResponse>(`/api/guidance/org?status=${status}`, {
        fallbackError: "Failed to load organization guidance",
      }),
    retry: retryTransientOnly,
  });
}

export function useProjectGuidance(projectId: string, status: GuidanceStatus = "active") {
  return useQuery({
    queryKey: guidanceKeys.project(projectId, status),
    queryFn: () =>
      fetchJson<GuidanceRuleListResponse>(
        `/api/guidance/projects/${encodeURIComponent(projectId)}?status=${status}`,
        { fallbackError: "Failed to load project guidance" },
      ),
    enabled: projectId.length > 0,
    retry: retryTransientOnly,
  });
}

export function usePersonPreferences(projectId: string) {
  return useQuery({
    queryKey: guidanceKeys.preferences(projectId),
    queryFn: () =>
      fetchJson<PersonPreferencesResponse>(
        `/api/guidance/projects/${encodeURIComponent(projectId)}/preferences`,
        { fallbackError: "Failed to load your answer preferences" },
      ),
    enabled: projectId.length > 0,
    retry: retryTransientOnly,
  });
}

export function useGuidancePreview(projectId: string) {
  return useQuery({
    queryKey: guidanceKeys.preview(projectId),
    queryFn: () =>
      fetchJson<GuidancePreviewResponse>(
        `/api/guidance/projects/${encodeURIComponent(projectId)}/preview`,
        { fallbackError: "Failed to render the guidance preview" },
      ),
    enabled: projectId.length > 0,
    retry: retryTransientOnly,
  });
}

// -- writes ------------------------------------------------------------------

function useGuidanceInvalidation() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: guidanceKeys.all });
}

export function useCreateOrgRule() {
  const invalidate = useGuidanceInvalidation();
  return useMutation({
    mutationFn: (draft: GuidanceRuleCreateRequest) =>
      fetchJson<GuidanceRule>("/api/guidance/org", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
        fallbackError: "Failed to add the organization rule",
      }),
    onSuccess: invalidate,
  });
}

export function useRetireOrgRule() {
  const invalidate = useGuidanceInvalidation();
  return useMutation({
    mutationFn: (ruleId: number) =>
      fetchJson<void>(`/api/guidance/org/${ruleId}`, {
        method: "DELETE",
        fallbackError: "Failed to retire the organization rule",
      }),
    onSuccess: invalidate,
  });
}

export function useCreateProjectRule(projectId: string) {
  const invalidate = useGuidanceInvalidation();
  return useMutation({
    mutationFn: (draft: GuidanceRuleCreateRequest) =>
      fetchJson<GuidanceRule>(`/api/guidance/projects/${encodeURIComponent(projectId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
        fallbackError: "Failed to add the project rule",
      }),
    onSuccess: invalidate,
  });
}

export function useRetireProjectRule(projectId: string) {
  const invalidate = useGuidanceInvalidation();
  return useMutation({
    mutationFn: (ruleId: number) =>
      fetchJson<void>(
        `/api/guidance/projects/${encodeURIComponent(projectId)}/rules/${ruleId}`,
        { method: "DELETE", fallbackError: "Failed to retire the project rule" },
      ),
    onSuccess: invalidate,
  });
}

export function useApproveProjectRule(projectId: string) {
  const invalidate = useGuidanceInvalidation();
  return useMutation({
    mutationFn: (ruleId: number) =>
      fetchJson<GuidanceRule>(
        `/api/guidance/projects/${encodeURIComponent(projectId)}/rules/${ruleId}/approve`,
        { method: "POST", fallbackError: "Failed to approve the proposed rule" },
      ),
    onSuccess: invalidate,
  });
}

export function useSavePersonPreferences(projectId: string) {
  const invalidate = useGuidanceInvalidation();
  return useMutation({
    mutationFn: (style: StylePreference) =>
      fetchJson<PersonPreferencesResponse>(
        `/api/guidance/projects/${encodeURIComponent(projectId)}/preferences`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(style),
          fallbackError: "Failed to save your answer preferences",
        },
      ),
    onSuccess: invalidate,
  });
}
