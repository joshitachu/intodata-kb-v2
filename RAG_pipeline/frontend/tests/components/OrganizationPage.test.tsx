import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrganizationPage from "@/app/admin/organization/page";

const replace = vi.fn();
let searchParams = new URLSearchParams();
let permissions = {
  isOwner: false,
  canManageUsers: true,
  canCreateProjects: true,
  hasPermission: () => true,
  hasAnyAdminPermission: true,
  canManageRoles: false,
  canAssignProjects: false,
  canIngestDocuments: false,
  canViewAuditLogs: false,
};

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => searchParams,
}));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => permissions,
}));

// The panels each own a large amount of fetch-driven markup; the dashboard
// contract under test is which tabs exist and which panel is mounted.
vi.mock("@/components/admin/org/UsersPanel", () => ({
  UsersPanel: () => <div>members-panel</div>,
}));
vi.mock("@/components/admin/org/AccessPanel", () => ({
  AccessPanel: () => <div>access-panel</div>,
}));
vi.mock("@/components/admin/org/RolesPanel", () => ({
  RolesPanel: () => <div>roles-panel</div>,
}));
vi.mock("@/components/admin/org/ProjectsPanel", () => ({
  ProjectsPanel: () => <div>projects-panel</div>,
}));

vi.mock("@/hooks/useAdminDirectory", () => ({
  useAdminUsers: () => ({ data: [{ id: 1, is_active: true }, { id: 2, is_active: false }], isLoading: false }),
  useAdminRoles: () => ({ data: [{ id: 1 }], isLoading: false }),
  useAdminAccessRequests: () => ({ data: { requests: [{ id: 1 }], invites: [] }, isLoading: false }),
  useAdminProjects: () => ({ data: [{ id: "a" }, { id: "b" }], isLoading: false }),
  useAdminAssignments: () => ({ data: [{ id: 1 }], isLoading: false }),
}));

function resetPermissions() {
  permissions = {
    isOwner: false,
    canManageUsers: true,
    canCreateProjects: true,
    hasPermission: () => true,
    hasAnyAdminPermission: true,
    canManageRoles: false,
    canAssignProjects: false,
    canIngestDocuments: false,
    canViewAuditLogs: false,
  };
}

describe("OrganizationPage", () => {
  beforeEach(() => {
    replace.mockClear();
    searchParams = new URLSearchParams();
    resetPermissions();
  });

  it("defaults to the members tab and summarizes the organization", () => {
    render(<OrganizationPage />);

    expect(screen.getByRole("tab", { name: /Members/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("members-panel")).toBeInTheDocument();

    // 2 users, 1 active.
    expect(screen.getByText("2 total · 1 inactive")).toBeInTheDocument();
    expect(screen.getByText("1 member assignments")).toBeInTheDocument();
  });

  it("hides the roles tab from non-owners and keeps tab state in the URL", () => {
    render(<OrganizationPage />);

    expect(screen.queryByRole("tab", { name: /Roles/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Projects/ }));
    expect(replace).toHaveBeenCalledWith("/admin/organization?tab=projects", { scroll: false });
  });

  it("renders the tab named in the query string", () => {
    searchParams = new URLSearchParams("tab=access");
    render(<OrganizationPage />);

    expect(screen.getByText("access-panel")).toBeInTheDocument();
  });

  it("shows the roles tab to the owner", () => {
    permissions = { ...permissions, isOwner: true };
    searchParams = new URLSearchParams("tab=roles");
    render(<OrganizationPage />);

    expect(screen.getByText("roles-panel")).toBeInTheDocument();
  });

  it("shows project administration to an admin with assignment permission", () => {
    permissions = {
      ...permissions,
      canManageUsers: false,
      canCreateProjects: false,
      canAssignProjects: true,
    };
    searchParams = new URLSearchParams("tab=projects");
    render(<OrganizationPage />);

    expect(screen.getByText("projects-panel")).toBeInTheDocument();
  });

  it("explains the denial when no section is reachable", () => {
    permissions = { ...permissions, canManageUsers: false, canCreateProjects: false };
    render(<OrganizationPage />);

    expect(
      screen.getByText("You do not have permission to administer this organization."),
    ).toBeInTheDocument();
  });
});
