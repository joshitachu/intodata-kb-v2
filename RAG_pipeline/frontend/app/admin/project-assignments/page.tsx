"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthContext } from "@/components/AuthProvider";
import { usePermissions } from "@/hooks/usePermissions";

interface AssignmentItem {
  id: number;
  user_id: number;
  username: string;
  project_id: string;
  is_owner: boolean;
  assigned_by: number | null;
  assigned_at: string;
}

interface UserOption {
  id: number;
  username: string;
}

interface ProjectOption {
  id: string;
  display_name: string;
  platform: string;
}

interface AssignForm {
  user_id: string;
  project_id: string;
}

export default function AdminProjectAssignmentsPage() {
  const { user: authUser } = useAuthContext();
  const { isSuperadmin, canCreateProjects } = usePermissions();
  const allowed = isSuperadmin || canCreateProjects;
  const router = useRouter();
  const [assignments, setAssignments] = useState<AssignmentItem[]>([]);
  const [users, setUsers] = useState<UserOption[]>([]);
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAssign, setShowAssign] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ project_id: "", display_name: "" });
  const [assignForm, setAssignForm] = useState<AssignForm>({
    user_id: "",
    project_id: "",
  });
  const [filterUser, setFilterUser] = useState("");
  const [filterProject, setFilterProject] = useState("");
  const [error, setError] = useState("");
  const [actionLoading, setActionLoading] = useState<number | null>(null);

  const fetchAssignments = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filterUser) params.set("user_id", filterUser);
      if (filterProject) params.set("project_id", filterProject);
      const queryString = params.toString();
      const url = `/api/admin/project-assignments${queryString ? `?${queryString}` : ""}`;
      const res = await fetch(url);
      if (res.ok) {
        const data: { assignments: AssignmentItem[] } = await res.json();
        setAssignments(data.assignments);
      }
    } catch {
      // ignore
    }
  }, [filterUser, filterProject]);

  const fetchUsers = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/users");
      if (res.ok) {
        const data: { users: UserOption[] } = await res.json();
        setUsers(data.users);
      }
    } catch {
      // ignore
    }
  }, []);

  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetch("/api/projects");
      if (res.ok) {
        const data: { projects: ProjectOption[] } = await res.json();
        setProjects(data.projects);
      }
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    Promise.all([fetchAssignments(), fetchUsers(), fetchProjects()]).then(() =>
      setLoading(false),
    );
  }, [fetchAssignments, fetchUsers, fetchProjects]);

  async function handleAssign(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const userId = parseInt(assignForm.user_id, 10);
    if (isNaN(userId)) {
      setError("Please select a user");
      return;
    }
    try {
      const res = await fetch("/api/admin/project-assignments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          project_id: assignForm.project_id,
        }),
      });
      if (!res.ok) {
        const data: { error?: string } | null = await res.json().catch(() => null);
        setError(data?.error || "Failed to assign project");
        return;
      }
      setShowAssign(false);
      setAssignForm({ user_id: "", project_id: "" });
      await fetchAssignments();
    } catch {
      setError("Network error");
    }
  }

  async function handleCreateProject(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const projectId = createForm.project_id.trim();
    if (!projectId) {
      setError("Enter a project id");
      return;
    }
    try {
      const res = await fetch("/api/admin/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          display_name: createForm.display_name.trim() || undefined,
        }),
      });
      if (!res.ok) {
        const data: { error?: string } | null = await res.json().catch(() => null);
        setError(data?.error || "Failed to create project");
        return;
      }
      setShowCreate(false);
      setCreateForm({ project_id: "", display_name: "" });
      await Promise.all([fetchAssignments(), fetchProjects()]);
    } catch {
      setError("Network error");
    }
  }

  async function handleUnassign(assignmentId: number) {
    if (!confirm("Are you sure you want to remove this assignment?")) return;
    setActionLoading(assignmentId);
    try {
      const res = await fetch(`/api/admin/project-assignments/${assignmentId}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const data: { error?: string } | null = await res.json().catch(() => null);
        setError(data?.error || "Failed to unassign project");
        return;
      }
      await fetchAssignments();
    } catch {
      setError("Network error");
    } finally {
      setActionLoading(null);
    }
  }

  // Map of project_id -> display_name for rendering assignments with human-readable names.
  // Falls back to the raw ID so rows for deleted/empty projects still render.
  const projectNameById = new Map(projects.map((p) => [p.id, p.display_name]));

  // Admins (create_projects) and superadmins may manage projects.
  useEffect(() => {
    if (authUser && !allowed) {
      router.replace("/admin/usage");
    }
  }, [authUser, allowed, router]);

  if (authUser && !allowed) {
    return <p className="text-muted-foreground p-4">Access denied.</p>;
  }

  if (loading) {
    return (
      <p className="text-muted-foreground p-4">Loading project assignments...</p>
    );
  }

  return (
    <div className="max-w-5xl mx-auto py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Projects</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              setShowCreate(!showCreate);
              setShowAssign(false);
              setError("");
            }}
            className="px-4 py-2 border text-sm rounded-md hover:bg-muted transition-colors"
          >
            {showCreate ? "Cancel" : "Create Project"}
          </button>
          <button
            onClick={() => {
              setShowAssign(!showAssign);
              setShowCreate(false);
              setError("");
            }}
            className="px-4 py-2 bg-primary text-primary-foreground text-sm rounded-md hover:bg-primary/90"
          >
            {showAssign ? "Cancel" : "Assign Project"}
          </button>
        </div>
      </div>

      {showCreate && (
        <form
          onSubmit={handleCreateProject}
          className="bg-white border rounded-lg p-4 mb-6 space-y-3"
        >
          <h3 className="font-medium text-sm text-gray-700">
            Create a project (you become its owner)
          </h3>
          <div className="grid grid-cols-2 gap-3">
            <input
              value={createForm.project_id}
              onChange={(e) =>
                setCreateForm({ ...createForm, project_id: e.target.value })
              }
              placeholder="project-id (e.g. talend-finance)"
              className="px-3 py-2 border rounded-md text-sm"
              required
            />
            <input
              value={createForm.display_name}
              onChange={(e) =>
                setCreateForm({ ...createForm, display_name: e.target.value })
              }
              placeholder="Display name (optional)"
              className="px-3 py-2 border rounded-md text-sm"
            />
          </div>
          <button
            type="submit"
            className="px-4 py-2 bg-green-600 text-white text-sm rounded-md hover:bg-green-700"
          >
            Create
          </button>
        </form>
      )}

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {showAssign && (
        <form
          onSubmit={handleAssign}
          className="bg-white border rounded-lg p-4 mb-6 space-y-3"
        >
          <h3 className="font-medium text-sm text-gray-700">
            Assign Project to User
          </h3>
          <div className="grid grid-cols-2 gap-3">
            <select
              value={assignForm.user_id}
              onChange={(e) =>
                setAssignForm({ ...assignForm, user_id: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
              required
            >
              <option value="">Select user...</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username} (ID: {u.id})
                </option>
              ))}
            </select>
            <select
              value={assignForm.project_id}
              onChange={(e) =>
                setAssignForm({ ...assignForm, project_id: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
              required
            >
              <option value="">Select project...</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            className="px-4 py-2 bg-green-600 text-white text-sm rounded-md hover:bg-green-700"
          >
            Assign
          </button>
        </form>
      )}

      {/* Filters */}
      <div className="flex gap-3 mb-4">
        <select
          value={filterUser}
          onChange={(e) => setFilterUser(e.target.value)}
          className="px-3 py-2 border rounded-md text-sm"
        >
          <option value="">All users</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.username}
            </option>
          ))}
        </select>
        <select
          value={filterProject}
          onChange={(e) => setFilterProject(e.target.value)}
          className="px-3 py-2 border rounded-md text-sm"
        >
          <option value="">All projects</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.display_name}
            </option>
          ))}
        </select>
        {(filterUser || filterProject) && (
          <button
            onClick={() => {
              setFilterUser("");
              setFilterProject("");
            }}
            className="px-3 py-2 text-sm text-gray-600 hover:text-gray-800"
          >
            Clear filters
          </button>
        )}
      </div>

      <div className="bg-white border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                ID
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                User
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Project
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Assigned By
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Date
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {assignments.map((a) => (
              <tr key={a.id} className="border-b last:border-0">
                <td className="px-4 py-3 text-gray-500">{a.id}</td>
                <td className="px-4 py-3 font-medium">
                  {a.username}
                  <span className="ml-1 text-xs text-gray-400">
                    (ID: {a.user_id})
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className="text-xs bg-primary/10 text-primary px-1.5 py-0.5 rounded">
                    {projectNameById.get(a.project_id) ?? a.project_id}
                  </span>
                  {a.is_owner && (
                    <span className="ml-1.5 text-xs bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded">
                      owner
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-gray-500">
                  {a.assigned_by !== null ? `User #${a.assigned_by}` : "-"}
                </td>
                <td className="px-4 py-3 text-gray-500 text-xs">
                  {a.assigned_at
                    ? new Date(a.assigned_at).toLocaleString()
                    : "-"}
                </td>
                <td className="px-4 py-3">
                  {a.is_owner ? (
                    <span className="text-xs text-gray-400">owner</span>
                  ) : (
                    <button
                      onClick={() => handleUnassign(a.id)}
                      disabled={actionLoading === a.id}
                      className="text-xs text-red-600 hover:text-red-800 disabled:opacity-50"
                    >
                      Unassign
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {assignments.length === 0 && (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-6 text-center text-gray-400"
                >
                  No assignments found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
