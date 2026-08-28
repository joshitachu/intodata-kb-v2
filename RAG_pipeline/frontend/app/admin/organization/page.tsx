"use client";

import { Suspense, useCallback, useMemo, type ReactElement } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { FolderKanban, KeyRound, UserPlus, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { usePermissions } from "@/hooks/usePermissions";
import { StatTile } from "@/components/admin/org/shared";
import { UsersPanel } from "@/components/admin/org/UsersPanel";
import { AccessPanel } from "@/components/admin/org/AccessPanel";
import { RolesPanel } from "@/components/admin/org/RolesPanel";
import { ProjectsPanel } from "@/components/admin/org/ProjectsPanel";
import {
  useAdminAccessRequests,
  useAdminAssignments,
  useAdminProjects,
  useAdminRoles,
  useAdminUsers,
} from "@/hooks/useAdminDirectory";

type TabId = "members" | "access" | "roles" | "projects";

interface TabDef {
  id: TabId;
  label: string;
  icon: typeof Users;
  render: () => ReactElement;
}

/**
 * Organization dashboard.
 *
 * Members, access requests, roles and project assignments were four separate
 * admin pages that all answer the same question — "who can reach what". They
 * are one page now: shared summary tiles on top, one tab per surface below.
 * Tab state lives in `?tab=` so a specific view stays linkable.
 */
export default function OrganizationPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading...</div>}>
      <OrganizationDashboard />
    </Suspense>
  );
}

function OrganizationDashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isOwner, canManageUsers, canCreateProjects } = usePermissions();

  const canManageProjects = isOwner || canCreateProjects;

  const tabs = useMemo<TabDef[]>(() => {
    const all: (TabDef & { visible: boolean })[] = [
      { id: "members", label: "Members", icon: Users, render: () => <UsersPanel />, visible: canManageUsers },
      { id: "access", label: "Access requests", icon: UserPlus, render: () => <AccessPanel />, visible: canManageUsers },
      { id: "roles", label: "Roles", icon: KeyRound, render: () => <RolesPanel />, visible: isOwner },
      { id: "projects", label: "Projects", icon: FolderKanban, render: () => <ProjectsPanel />, visible: canManageProjects },
    ];
    return all.filter((tab) => tab.visible);
  }, [canManageUsers, isOwner, canManageProjects]);

  const requested = searchParams.get("tab") as TabId | null;
  const active = tabs.find((tab) => tab.id === requested)?.id ?? tabs[0]?.id;

  const selectTab = useCallback(
    (tab: TabId) => router.replace(`/admin/organization?tab=${tab}`, { scroll: false }),
    [router],
  );

  // Summary reads use the same query keys as the panels, so opening a tab does
  // not re-fetch what the tiles already loaded.
  const usersQuery = useAdminUsers(canManageUsers);
  const rolesQuery = useAdminRoles(canManageUsers || isOwner);
  const pendingQuery = useAdminAccessRequests("pending", canManageUsers);
  const projectsQuery = useAdminProjects(canManageProjects);
  const assignmentsQuery = useAdminAssignments("", "", canManageProjects);

  const users = usersQuery.data ?? [];
  const activeUsers = users.filter((user) => user.is_active).length;
  const pendingCount = pendingQuery.data?.requests.length ?? 0;

  if (tabs.length === 0) {
    return (
      <main className="mx-auto max-w-6xl p-6">
        <p className="text-sm text-muted-foreground">
          You do not have permission to administer this organization.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-6xl space-y-6 p-4 sm:p-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">Organization</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Members, access requests, roles and project assignments — everything that decides who can
          reach what.
        </p>
      </header>

      <section aria-label="Summary" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {canManageUsers && (
          <StatTile
            label="Members"
            value={usersQuery.isLoading ? "—" : activeUsers}
            hint={
              usersQuery.isLoading
                ? "Loading"
                : `${users.length} total · ${users.length - activeUsers} inactive`
            }
            icon={Users}
          />
        )}
        {canManageUsers && (
          <StatTile
            label="Pending access"
            value={pendingQuery.isLoading ? "—" : pendingCount}
            hint={pendingCount === 0 ? "Nothing waiting" : "Awaiting a decision"}
            icon={UserPlus}
            tone={pendingCount > 0 ? "attention" : "default"}
          />
        )}
        <StatTile
          label="Roles"
          value={rolesQuery.isLoading ? "—" : rolesQuery.data?.length ?? 0}
          hint={isOwner ? "Owner-managed definitions" : "Read-only"}
          icon={KeyRound}
        />
        {canManageProjects && (
          <StatTile
            label="Projects"
            value={projectsQuery.isLoading ? "—" : projectsQuery.data?.length ?? 0}
            hint={
              assignmentsQuery.isLoading
                ? "Loading"
                : `${assignmentsQuery.data?.length ?? 0} member assignments`
            }
            icon={FolderKanban}
          />
        )}
      </section>

      <div
        role="tablist"
        aria-label="Organization sections"
        className="flex flex-wrap gap-1 rounded-xl border border-border bg-muted/30 p-1"
      >
        {tabs.map(({ id, label, icon: Icon }) => {
          const isActive = id === active;
          const badge = id === "access" && pendingCount > 0 ? pendingCount : null;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => selectTab(id)}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                isActive
                  ? "bg-card text-foreground shadow-elevation-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {label}
              {badge !== null && (
                <span className="rounded-full bg-amber-100 px-1.5 text-[11px] font-semibold text-amber-800">
                  {badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <section
        role="tabpanel"
        aria-label={tabs.find((tab) => tab.id === active)?.label}
        // Anchors the walkthrough to the panel that is actually open, so an
        // admin step spotlights the content rather than the tab strip.
        data-tour={`admin-${active}`}
      >
        {tabs.find((tab) => tab.id === active)?.render()}
      </section>
    </main>
  );
}
