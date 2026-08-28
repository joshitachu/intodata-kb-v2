import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApproveAccessDialog } from "@/components/admin/org/ApproveAccessDialog";
import type { AdminAccessRequest } from "@/hooks/useAdminDirectory";

/**
 * Approval is where admin authority is granted, so the dialog has to make that
 * choice explicit, warn when it is elevated, and refuse to submit without one.
 */

let roles: unknown[] = [];
let projects: unknown[] = [];
let canAssignProjects = true;

vi.mock("@/hooks/useAdminDirectory", () => ({
  useAdminRoles: () => ({ data: roles, isLoading: false, isError: false }),
  useAdminProjects: () => ({ data: projects, isLoading: false, isError: false }),
}));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAssignProjects }),
}));

const REQUEST: AdminAccessRequest = {
  id: 11,
  email: "newcomer@example.com",
  full_name: "New Comer",
  status: "pending",
  rejection_reason: null,
  created_at: "2026-08-27T10:00:00Z",
  invite: null,
};

describe("ApproveAccessDialog", () => {
  beforeEach(() => {
    canAssignProjects = true;
    projects = [{ id: "werkorder", display_name: "Werkorder", platform: "talend" }];
    roles = [
      {
        id: 2,
        name: "developer",
        description: "Read and query assigned projects",
        permissions: [{ id: 1, codename: "query_rag", description: null }],
      },
      {
        id: 3,
        name: "admin",
        description: "Administer members and projects",
        permissions: [{ id: 2, codename: "manage_users", description: null }],
      },
      {
        id: 9,
        name: "owner",
        description: "Deployment owner",
        permissions: [],
      },
    ];
  });

  it("does not offer Owner authority as an assignable role", () => {
    render(
      <ApproveAccessDialog
        request={REQUEST}
        isBusy={false}
        onClose={vi.fn()}
        onApprove={vi.fn()}
      />,
    );

    expect(screen.getByText("developer")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
    // The backend refuses this, so presenting it would only ever fail.
    expect(screen.queryByText("owner")).not.toBeInTheDocument();
  });

  it("blocks submission until a role is chosen", () => {
    render(
      <ApproveAccessDialog
        request={REQUEST}
        isBusy={false}
        onClose={vi.fn()}
        onApprove={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Send invitation" })).toBeDisabled();

    fireEvent.click(screen.getByText("developer"));
    expect(screen.getByRole("button", { name: "Send invitation" })).toBeEnabled();
  });

  it("warns that an administration role grants member administration", () => {
    render(
      <ApproveAccessDialog
        request={REQUEST}
        isBusy={false}
        onClose={vi.fn()}
        onApprove={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("developer"));
    expect(screen.getByText(/Standard member access/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("admin"));
    expect(screen.getByText(/This grants administration/)).toBeInTheDocument();
    expect(screen.getByText(/recorded in the audit log/)).toBeInTheDocument();
  });

  it("reports the chosen role, projects and note", () => {
    const onApprove = vi.fn();
    render(
      <ApproveAccessDialog
        request={REQUEST}
        isBusy={false}
        onClose={vi.fn()}
        onApprove={onApprove}
      />,
    );

    fireEvent.click(screen.getByText("admin"));
    fireEvent.click(screen.getByRole("checkbox", { name: "Werkorder" }));
    fireEvent.change(screen.getByLabelText(/Welcome note/), {
      target: { value: "  Joining the platform team  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }));

    expect(onApprove).toHaveBeenCalledWith({
      role_id: 3,
      project_ids: ["werkorder"],
      // Trimmed, so trailing whitespace does not become part of the greeting.
      welcome_note: "Joining the platform team",
    });
  });

  it("sends a null note when none was written", () => {
    const onApprove = vi.fn();
    render(
      <ApproveAccessDialog
        request={REQUEST}
        isBusy={false}
        onClose={vi.fn()}
        onApprove={onApprove}
      />,
    );

    fireEvent.click(screen.getByText("developer"));
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }));

    expect(onApprove).toHaveBeenCalledWith({
      role_id: 2,
      project_ids: [],
      welcome_note: null,
    });
  });

  it("hides project pre-assignment without the assign_projects permission", () => {
    canAssignProjects = false;
    render(
      <ApproveAccessDialog
        request={REQUEST}
        isBusy={false}
        onClose={vi.fn()}
        onApprove={vi.fn()}
      />,
    );

    // The backend rejects it, so offering the control would be a dead end.
    expect(screen.queryByRole("checkbox", { name: "Werkorder" })).not.toBeInTheDocument();
    expect(screen.getByText("Role on joining")).toBeInTheDocument();
  });

  it("renders nothing when no request is being approved", () => {
    const { container } = render(
      <ApproveAccessDialog
        request={null}
        isBusy={false}
        onClose={vi.fn()}
        onApprove={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
