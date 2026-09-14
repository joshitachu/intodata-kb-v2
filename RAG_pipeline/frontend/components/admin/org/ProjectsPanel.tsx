"use client";

import { useState, type FormEvent } from "react";
import { FolderKanban, Plus, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SelectMenu, type SelectMenuOption } from "@/components/ui/select-menu";
import {
  ActionCell,
  Alert,
  Cell,
  Chip,
  DataTable,
  InlineAction,
  Modal,
  PanelHeader,
  PanelLoading,
  Row,
  Toast,
} from "@/components/admin/org/shared";
import {
  adminKeys,
  adminMutate,
  useAdminAssignments,
  useAdminProjects,
  useAdminRefresh,
  useAdminUsers,
  type AdminAssignment,
} from "@/hooks/useAdminDirectory";

type FormMode = "none" | "create" | "assign";

interface ProjectsPanelProps {
  canCreateProjects?: boolean;
}

/** Projects and the member assignments that grant access to them. */
export function ProjectsPanel({ canCreateProjects = true }: ProjectsPanelProps) {
  const [filterUser, setFilterUser] = useState("");
  const [filterProject, setFilterProject] = useState("");

  const usersQuery = useAdminUsers();
  const projectsQuery = useAdminProjects();
  const assignmentsQuery = useAdminAssignments(filterUser, filterProject);
  const refresh = useAdminRefresh();

  const [formMode, setFormMode] = useState<FormMode>("none");
  const [createForm, setCreateForm] = useState({ project_id: "", display_name: "" });
  const [assignForm, setAssignForm] = useState({ user_id: "", project_id: "" });
  const [unassignTarget, setUnassignTarget] = useState<AdminAssignment | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  const users = usersQuery.data ?? [];
  const projects = projectsQuery.data ?? [];
  const assignments = assignmentsQuery.data ?? [];

  // Falls back to the raw ID so rows for deleted projects still render.
  const projectNameById = new Map(projects.map((project) => [project.id, project.display_name]));

  const userOptions: SelectMenuOption[] = users.map((user) => ({
    value: String(user.id),
    label: user.username,
    hint: user.email,
  }));
  const projectOptions: SelectMenuOption[] = projects.map((project) => ({
    value: project.id,
    label: project.display_name,
    hint: project.platform,
  }));

  async function run(work: () => Promise<string>, assignmentId?: number) {
    setError("");
    if (assignmentId) setBusyId(assignmentId);
    try {
      const message = await work();
      await refresh(["admin", "assignments"], adminKeys.projects);
      setSuccess(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Request failed");
    } finally {
      setBusyId(null);
    }
  }

  async function handleCreateProject(event: FormEvent) {
    event.preventDefault();
    const projectId = createForm.project_id.trim();
    if (!projectId) {
      setError("Enter a project id");
      return;
    }
    await run(async () => {
      await adminMutate(
        "/api/admin/projects",
        {
          method: "POST",
          body: { project_id: projectId, display_name: createForm.display_name.trim() || undefined },
        },
        "Failed to create project",
      );
      setFormMode("none");
      setCreateForm({ project_id: "", display_name: "" });
      return `Project "${projectId}" created`;
    });
  }

  async function handleAssign(event: FormEvent) {
    event.preventDefault();
    const userId = Number.parseInt(assignForm.user_id, 10);
    if (Number.isNaN(userId) || !assignForm.project_id) {
      setError("Select both a member and a project");
      return;
    }
    await run(async () => {
      await adminMutate(
        "/api/admin/project-assignments",
        { method: "POST", body: { user_id: userId, project_id: assignForm.project_id } },
        "Failed to assign project",
      );
      setFormMode("none");
      setAssignForm({ user_id: "", project_id: "" });
      return "Project assigned";
    });
  }

  async function handleUnassign() {
    if (!unassignTarget) return;
    const target = unassignTarget;
    setUnassignTarget(null);
    await run(async () => {
      await adminMutate(
        `/api/admin/project-assignments/${target.id}`,
        { method: "DELETE" },
        "Failed to unassign project",
      );
      return `${target.username} removed from ${projectNameById.get(target.project_id) ?? target.project_id}`;
    }, target.id);
  }

  if (projectsQuery.isLoading && assignmentsQuery.isLoading) {
    return <PanelLoading label="Loading projects..." />;
  }

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Projects & assignments"
        description="Create projects and control which members can reach their documents and scans."
        actions={
          <>
            {canCreateProjects && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => { setFormMode((mode) => (mode === "create" ? "none" : "create")); setError(""); }}
              >
                <Plus className="h-3.5 w-3.5" aria-hidden />
                New project
              </Button>
            )}
            <Button
              size="sm"
              onClick={() => { setFormMode((mode) => (mode === "assign" ? "none" : "assign")); setError(""); }}
            >
              <UserPlus className="h-3.5 w-3.5" aria-hidden />
              Assign member
            </Button>
          </>
        }
      />

      {(projectsQuery.isError || assignmentsQuery.isError) && (
        <Alert tone="error">
          {(projectsQuery.error ?? assignmentsQuery.error)?.message ?? "Failed to load projects"}
        </Alert>
      )}
      {error && <Alert tone="error" onDismiss={() => setError("")}>{error}</Alert>}
      <Toast message={success} onClear={() => setSuccess("")} />

      {formMode === "create" && (
        <form onSubmit={handleCreateProject} className="space-y-3 rounded-xl border border-border bg-muted/20 p-4">
          <p className="text-xs font-medium text-muted-foreground">
            You become the owner of the project you create.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
              Project id
              <Input
                value={createForm.project_id}
                onChange={(event) => setCreateForm({ ...createForm, project_id: event.target.value })}
                placeholder="talend-finance"
                required
              />
            </label>
            <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
              Display name (optional)
              <Input
                value={createForm.display_name}
                onChange={(event) => setCreateForm({ ...createForm, display_name: event.target.value })}
                placeholder="Talend Finance"
              />
            </label>
          </div>
          <Button type="submit" size="sm">Create project</Button>
        </form>
      )}

      {formMode === "assign" && (
        <form onSubmit={handleAssign} className="space-y-3 rounded-xl border border-border bg-muted/20 p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">Member</p>
              <SelectMenu
                value={assignForm.user_id}
                onChange={(value) => setAssignForm({ ...assignForm, user_id: value })}
                options={userOptions}
                placeholder="Select a member"
                ariaLabel="Member to assign"
                variant="field"
                size="md"
                className="w-full max-w-none"
              />
            </div>
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">Project</p>
              <SelectMenu
                value={assignForm.project_id}
                onChange={(value) => setAssignForm({ ...assignForm, project_id: value })}
                options={projectOptions}
                placeholder="Select a project"
                ariaLabel="Project to assign"
                variant="field"
                size="md"
                className="w-full max-w-none"
              />
            </div>
          </div>
          <Button type="submit" size="sm">Assign</Button>
        </form>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <SelectMenu
          value={filterUser}
          onChange={setFilterUser}
          options={[{ value: "", label: "All members" }, ...userOptions]}
          placeholder="All members"
          ariaLabel="Filter by member"
          variant="field"
        />
        <SelectMenu
          value={filterProject}
          onChange={setFilterProject}
          options={[{ value: "", label: "All projects" }, ...projectOptions]}
          placeholder="All projects"
          icon={FolderKanban}
          ariaLabel="Filter by project"
          variant="field"
        />
        {(filterUser || filterProject) && (
          <InlineAction onClick={() => { setFilterUser(""); setFilterProject(""); }}>
            Clear filters
          </InlineAction>
        )}
      </div>

      <DataTable
        columns={["Member", "Project", "Assigned by", "Assigned", "Actions"]}
        empty="No assignments match this filter."
        isEmpty={assignments.length === 0}
      >
        {assignments.map((assignment) => (
          <Row key={assignment.id}>
            <Cell>
              <span className="font-medium text-foreground">{assignment.username}</span>
              <p className="mt-0.5 text-xs text-muted-foreground">User #{assignment.user_id}</p>
            </Cell>
            <Cell>
              <div className="flex flex-wrap items-center gap-1">
                <Chip>{projectNameById.get(assignment.project_id) ?? assignment.project_id}</Chip>
                {assignment.is_owner && <Chip tone="owner">owner</Chip>}
              </div>
            </Cell>
            <Cell muted className="text-xs">
              {assignment.assigned_by !== null ? `User #${assignment.assigned_by}` : "-"}
            </Cell>
            <Cell muted className="text-xs">
              {assignment.assigned_at ? new Date(assignment.assigned_at).toLocaleString() : "-"}
            </Cell>
            <ActionCell>
              {assignment.is_owner ? (
                <span className="px-2 text-xs text-muted-foreground">Protected</span>
              ) : (
                <InlineAction
                  tone="danger"
                  onClick={() => setUnassignTarget(assignment)}
                  disabled={busyId === assignment.id}
                >
                  Unassign
                </InlineAction>
              )}
            </ActionCell>
          </Row>
        ))}
      </DataTable>

      <Modal
        open={unassignTarget !== null}
        title="Remove project assignment"
        description={`${unassignTarget?.username ?? ""} loses access to ${
          unassignTarget ? projectNameById.get(unassignTarget.project_id) ?? unassignTarget.project_id : ""
        }.`}
        onClose={() => setUnassignTarget(null)}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setUnassignTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" size="sm" onClick={() => void handleUnassign()}>
              Unassign
            </Button>
          </>
        }
      >
        <span className="sr-only">Confirm unassignment</span>
      </Modal>
    </div>
  );
}
