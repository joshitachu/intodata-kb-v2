"use client";

import { useCallback, useEffect, useState } from "react";

interface AssignmentItem {
  id: number;
  user_id: number;
  username: string;
  project_id: string;
  assigned_by: number | null;
  assigned_at: string;
}

interface UserOption {
  id: number;
  username: string;
}

interface AssignForm {
  user_id: string;
  project_id: string;
}

export default function AdminProjectAssignmentsPage() {
  const [assignments, setAssignments] = useState<AssignmentItem[]>([]);
  const [users, setUsers] = useState<UserOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAssign, setShowAssign] = useState(false);
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

  useEffect(() => {
    Promise.all([fetchAssignments(), fetchUsers()]).then(() =>
      setLoading(false),
    );
  }, [fetchAssignments, fetchUsers]);

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

  // Get unique project IDs for the filter dropdown
  const uniqueProjects = Array.from(
    new Set(assignments.map((a) => a.project_id)),
  ).sort();

  if (loading) {
    return (
      <p className="text-muted-foreground p-4">Loading project assignments...</p>
    );
  }

  return (
    <div className="max-w-5xl mx-auto py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Project Assignments</h1>
        <button
          onClick={() => {
            setShowAssign(!showAssign);
            setError("");
          }}
          className="px-4 py-2 bg-[#001e4d] text-white text-sm rounded-md hover:bg-[#001e4d]/90"
        >
          {showAssign ? "Cancel" : "Assign Project"}
        </button>
      </div>

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
            <input
              type="text"
              placeholder="Project ID"
              value={assignForm.project_id}
              onChange={(e) =>
                setAssignForm({ ...assignForm, project_id: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
              required
            />
          </div>
          <button
            type="submit"
            className="px-4 py-2 bg-[#a0e06f] text-[#060e1e] text-sm rounded-md hover:bg-[#a0e06f]/90"
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
          {uniqueProjects.map((pid) => (
            <option key={pid} value={pid}>
              {pid}
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
                  <span className="text-xs bg-[#001e4d]/10 text-[#001e4d] px-1.5 py-0.5 rounded">
                    {a.project_id}
                  </span>
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
                  <button
                    onClick={() => handleUnassign(a.id)}
                    disabled={actionLoading === a.id}
                    className="text-xs text-red-600 hover:text-red-800 disabled:opacity-50"
                  >
                    Unassign
                  </button>
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
