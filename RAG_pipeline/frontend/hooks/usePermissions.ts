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
  | "view_audit_logs"
  | "create_projects";

interface UsePermissionsReturn {
  /** Check if the current user has a specific permission (Owner bypasses). */
  hasPermission: (codename: PermissionCodename) => boolean;
  /** True for the deployment Owner (persisted as is_superadmin). */
  isOwner: boolean;
  /** True if the user should see admin UI (Owner or Admin role). */
  hasAnyAdminPermission: boolean;
  canManageUsers: boolean;
  canManageRoles: boolean;
  canAssignProjects: boolean;
  canCreateProjects: boolean;
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

  const isOwner = user?.is_superadmin ?? false;

  return {
    hasPermission,
    isOwner,
    hasAnyAdminPermission: isOwner || (user?.roles?.includes("admin") ?? false),
    canManageUsers: hasPermission("manage_users"),
    canManageRoles: hasPermission("manage_roles"),
    canAssignProjects: hasPermission("assign_projects"),
    canCreateProjects: hasPermission("create_projects"),
    canIngestDocuments: hasPermission("ingest_documents"),
    canViewAuditLogs: hasPermission("view_audit_logs"),
  };
}
