"use client";

import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useCallback } from "react";
import { apiErrorMessage } from "@/lib/apiError";
import { authedFetch } from "@/lib/authedFetch";

/**
 * Shared data layer for the Organization admin dashboard.
 *
 * Users, roles, access requests, projects and assignments are read by both the
 * summary tiles and the individual panels. Routing every read through React
 * Query with a stable key means the dashboard issues one request per resource
 * regardless of how many panels are mounted, and a mutation in one panel
 * refreshes every dependent view.
 */

export interface AdminRole {
  id: number;
  name: string;
  description: string | null;
  permissions: AdminPermission[];
}

export interface AdminPermission {
  id: number;
  codename: string;
  description: string | null;
}

export interface AdminUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  is_superadmin: boolean;
  roles: { id: number; name: string }[];
  last_login_at: string | null;
}

export interface AdminInvite {
  id: number;
  email: string;
  status: string;
  expires_at: string;
  accepted_user_id: number | null;
  /**
   * Authority the approver chose, applied when the invite is accepted.
   * Null means the invitee will arrive with the default member role.
   */
  assigned_role_id?: number | null;
  assigned_role_name?: string | null;
  assigned_project_ids?: string[];
  welcome_note?: string | null;
}

export interface AdminAccessRequest {
  id: number;
  email: string;
  full_name: string | null;
  status: string;
  rejection_reason: string | null;
  created_at: string;
  invite: AdminInvite | null;
}

export interface AdminProject {
  id: string;
  display_name: string;
  platform: string;
}

export interface AdminAssignment {
  id: number;
  user_id: number;
  username: string;
  project_id: string;
  is_owner: boolean;
  assigned_by: number | null;
  assigned_at: string;
}

/** Every query key used by the dashboard, so panels can invalidate precisely. */
export const adminKeys = {
  users: ["admin", "users"] as const,
  roles: ["admin", "roles"] as const,
  permissions: ["admin", "permissions"] as const,
  accessRequests: (status: string) => ["admin", "access-requests", status] as const,
  projects: ["admin", "projects"] as const,
  assignments: (userId: string, projectId: string) =>
    ["admin", "assignments", userId, projectId] as const,
};

// `authedFetch` rather than bare `fetch`: the Organization dashboard fires
// every tile at once, so on a cold load inside the stale-access-token window
// each read independently discovered a 401 and had no way to recover from it.
// `authedFetch` rotates the session once for all of them and replays. It is
// used instead of `fetchJson` because both helpers here inspect the payload
// themselves (`error` then `detail`) to build the panel's alert message.
async function getJson<T>(url: string, fallback: string): Promise<T> {
  const res = await authedFetch(url, { credentials: "include" });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(
      (payload as { error?: string })?.error ??
        apiErrorMessage((payload as { detail?: unknown })?.detail, fallback),
    );
  }
  return payload as T;
}

/**
 * Issue a write and surface a usable message on failure.
 * Panels catch the thrown Error and render it in their alert bar.
 */
export async function adminMutate<T = unknown>(
  url: string,
  init: { method: "POST" | "PUT" | "DELETE"; body?: unknown },
  fallback: string,
): Promise<T> {
  const res = await authedFetch(url, {
    method: init.method,
    credentials: "include",
    ...(init.body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(init.body),
        }),
  });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(
      (payload as { error?: string })?.error ??
        apiErrorMessage((payload as { detail?: unknown })?.detail, fallback),
    );
  }
  return payload as T;
}

export function useAdminUsers(enabled = true): UseQueryResult<AdminUser[], Error> {
  return useQuery({
    queryKey: adminKeys.users,
    enabled,
    queryFn: async () =>
      (await getJson<{ users: AdminUser[] }>("/api/admin/users", "Failed to load users")).users,
  });
}

export function useAdminRoles(enabled = true): UseQueryResult<AdminRole[], Error> {
  return useQuery({
    queryKey: adminKeys.roles,
    enabled,
    queryFn: async () =>
      (await getJson<{ roles: AdminRole[] }>("/api/admin/roles", "Failed to load roles")).roles,
  });
}

export function useAdminPermissions(enabled = true): UseQueryResult<AdminPermission[], Error> {
  return useQuery({
    queryKey: adminKeys.permissions,
    enabled,
    queryFn: async () =>
      (
        await getJson<{ permissions: AdminPermission[] }>(
          "/api/admin/permissions",
          "Failed to load permissions",
        )
      ).permissions,
  });
}

export interface AccessRequestsData {
  requests: AdminAccessRequest[];
  invites: AdminInvite[];
}

export function useAdminAccessRequests(
  status: string,
  enabled = true,
): UseQueryResult<AccessRequestsData, Error> {
  return useQuery({
    queryKey: adminKeys.accessRequests(status),
    enabled,
    queryFn: async () => {
      const qs = status ? `?status=${encodeURIComponent(status)}` : "";
      const data = await getJson<Partial<AccessRequestsData>>(
        `/api/admin/access-requests${qs}`,
        "Failed to load access requests",
      );
      return { requests: data.requests ?? [], invites: data.invites ?? [] };
    },
  });
}

export function useAdminProjects(enabled = true): UseQueryResult<AdminProject[], Error> {
  return useQuery({
    queryKey: adminKeys.projects,
    enabled,
    queryFn: async () =>
      (await getJson<{ projects: AdminProject[] }>("/api/admin/projects", "Failed to load projects"))
        .projects,
  });
}

export function useAdminAssignments(
  userId: string,
  projectId: string,
  enabled = true,
): UseQueryResult<AdminAssignment[], Error> {
  return useQuery({
    queryKey: adminKeys.assignments(userId, projectId),
    enabled,
    queryFn: async () => {
      const params = new URLSearchParams();
      if (userId) params.set("user_id", userId);
      if (projectId) params.set("project_id", projectId);
      const qs = params.toString();
      return (
        await getJson<{ assignments: AdminAssignment[] }>(
          `/api/admin/project-assignments${qs ? `?${qs}` : ""}`,
          "Failed to load project assignments",
        )
      ).assignments;
    },
  });
}

/** Invalidate one or more admin resources after a write. */
export function useAdminRefresh(): (...prefixes: readonly (readonly unknown[])[]) => Promise<void> {
  const queryClient = useQueryClient();
  return useCallback(
    async (...prefixes: readonly (readonly unknown[])[]) => {
      await Promise.all(
        prefixes.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      );
    },
    [queryClient],
  );
}
