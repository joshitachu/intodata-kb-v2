"use client";

import { useCallback, useEffect, useState } from "react";

interface Role {
  id: number;
  name: string;
}

interface AdminUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  is_superadmin: boolean;
  roles: Role[];
  last_login_at: string | null;
}

interface CreateFormData {
  username: string;
  email: string;
  password: string;
  role_ids: number[];
}

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateFormData>({
    username: "",
    email: "",
    password: "",
    role_ids: [],
  });
  const [error, setError] = useState("");
  const [actionLoading, setActionLoading] = useState<number | null>(null);

  const fetchUsers = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/users");
      if (res.ok) {
        const data = await res.json();
        setUsers(data.users);
      }
    } catch {
      // ignore
    }
  }, []);

  const fetchRoles = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/roles");
      if (res.ok) {
        const data = await res.json();
        setRoles(data.roles);
      }
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    Promise.all([fetchUsers(), fetchRoles()]).then(() => setLoading(false));
  }, [fetchUsers, fetchRoles]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await fetch("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(createForm),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setError(data?.error || "Failed to create user");
        return;
      }
      setShowCreate(false);
      setCreateForm({ username: "", email: "", password: "", role_ids: [] });
      await fetchUsers();
    } catch {
      setError("Network error");
    }
  }

  async function handleDeactivate(userId: number) {
    setActionLoading(userId);
    try {
      await fetch(`/api/admin/users/${userId}`, { method: "DELETE" });
      await fetchUsers();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleActivate(userId: number) {
    setActionLoading(userId);
    try {
      await fetch(`/api/admin/users/${userId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: true }),
      });
      await fetchUsers();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleResetPassword(userId: number) {
    const newPassword = prompt("Enter new password (min 4 chars):");
    if (!newPassword || newPassword.length < 4) return;
    setActionLoading(userId);
    try {
      await fetch(`/api/admin/users/${userId}/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: newPassword }),
      });
      alert("Password reset successfully");
    } finally {
      setActionLoading(null);
    }
  }

  if (loading) {
    return <p className="text-muted-foreground p-4">Loading users...</p>;
  }

  return (
    <div className="max-w-5xl mx-auto py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">User Management</h1>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700"
        >
          {showCreate ? "Cancel" : "Create User"}
        </button>
      </div>

      {showCreate && (
        <form
          onSubmit={handleCreate}
          className="bg-white border rounded-lg p-4 mb-6 space-y-3"
        >
          <div className="grid grid-cols-3 gap-3">
            <input
              type="text"
              placeholder="Username"
              value={createForm.username}
              onChange={(e) =>
                setCreateForm({ ...createForm, username: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
              required
            />
            <input
              type="email"
              placeholder="Email"
              value={createForm.email}
              onChange={(e) =>
                setCreateForm({ ...createForm, email: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
              required
            />
            <input
              type="password"
              placeholder="Password"
              value={createForm.password}
              onChange={(e) =>
                setCreateForm({ ...createForm, password: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
              required
              minLength={4}
            />
          </div>
          {roles.length > 0 && (
            <div className="flex gap-2 flex-wrap">
              {roles.map((role) => (
                <label key={role.id} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={createForm.role_ids.includes(role.id)}
                    onChange={(e) => {
                      const ids = e.target.checked
                        ? [...createForm.role_ids, role.id]
                        : createForm.role_ids.filter((id) => id !== role.id);
                      setCreateForm({ ...createForm, role_ids: ids });
                    }}
                  />
                  {role.name}
                </label>
              ))}
            </div>
          )}
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            className="px-4 py-2 bg-green-600 text-white text-sm rounded-md hover:bg-green-700"
          >
            Create
          </button>
        </form>
      )}

      <div className="bg-white border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-3 font-medium text-gray-600">ID</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Username
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">Email</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">Roles</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Status
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Last Login
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b last:border-0">
                <td className="px-4 py-3 text-gray-500">{u.id}</td>
                <td className="px-4 py-3 font-medium">
                  {u.username}
                  {u.is_superadmin && (
                    <span className="ml-1 text-xs text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded">
                      admin
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-gray-600">{u.email}</td>
                <td className="px-4 py-3">
                  <div className="flex gap-1 flex-wrap">
                    {u.roles.map((r) => (
                      <span
                        key={r.id}
                        className="text-xs bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded"
                      >
                        {r.name}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`text-xs px-2 py-0.5 rounded ${
                      u.is_active
                        ? "bg-green-50 text-green-700"
                        : "bg-red-50 text-red-700"
                    }`}
                  >
                    {u.is_active ? "Active" : "Inactive"}
                  </span>
                </td>
                <td className="px-4 py-3 text-gray-500 text-xs">
                  {u.last_login_at
                    ? new Date(u.last_login_at).toLocaleString()
                    : "Never"}
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleResetPassword(u.id)}
                      disabled={actionLoading === u.id}
                      className="text-xs text-blue-600 hover:text-blue-800 disabled:opacity-50"
                    >
                      Reset PW
                    </button>
                    {u.is_active ? (
                      <button
                        onClick={() => handleDeactivate(u.id)}
                        disabled={actionLoading === u.id || u.is_superadmin}
                        className="text-xs text-red-600 hover:text-red-800 disabled:opacity-50"
                        title={u.is_superadmin ? "Cannot deactivate admin" : ""}
                      >
                        Deactivate
                      </button>
                    ) : (
                      <button
                        onClick={() => handleActivate(u.id)}
                        disabled={actionLoading === u.id}
                        className="text-xs text-green-600 hover:text-green-800 disabled:opacity-50"
                      >
                        Activate
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
