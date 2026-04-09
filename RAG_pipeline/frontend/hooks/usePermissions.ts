"use client";

import { useAuthContext } from "@/components/AuthProvider";

/** All known permission codenames from the RBAC system. */
type PermissionCodename =
  | "manage_users"
  | "manage_roles"
  | "assign_projects"
  | "view_all_projects"
  | "manage_projects"
  | "view_assigned_projects"
  | "query_rag"
  | "ingest_documents"
  | "view_audit_logs";

interface UsePermissionsReturn {
  /** Check if the current user has a specific permission (superadmin bypasses). */
  hasPermission: (codename: PermissionCodename) => boolean;
  /** True if the user is a superadmin. */
  isSuperadmin: boolean;
  /** True if the user should see admin UI (superadmin or admin role). */
  hasAnyAdminPermission: boolean;
  canManageUsers: boolean;
  canManageRoles: boolean;
  canAssignProjects: boolean;
  canIngestDocuments: boolean;
  canViewAuditLogs: boolean;
}

export function usePermissions(): UsePermissionsReturn {
  const { user } = useAuthContext();

  const hasPermission = (codename: PermissionCodename): boolean => {
    if (!user) return false;
    if (user.is_superadmin) return true;
    return user.permissions?.includes(codename) ?? false;
  };

  const isSuperadmin = user?.is_superadmin ?? false;

  return {
    hasPermission,
    isSuperadmin,
    hasAnyAdminPermission: isSuperadmin || (user?.roles?.includes("admin") ?? false),
    canManageUsers: hasPermission("manage_users"),
    canManageRoles: hasPermission("manage_roles"),
    canAssignProjects: hasPermission("assign_projects"),
    canIngestDocuments: hasPermission("ingest_documents"),
    canViewAuditLogs: hasPermission("view_audit_logs"),
  };
}
