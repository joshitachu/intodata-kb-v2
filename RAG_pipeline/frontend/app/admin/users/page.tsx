"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuthContext } from "@/components/AuthProvider";
import { useRouter } from "next/navigation";

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

interface PasswordResetState {
  userId: number;
  username: string;
  password: string;
  confirm: string;
}

interface EditRolesState {
  userId: number;
  username: string;
  role_ids: number[];
}

export default function AdminUsersPage() {
  const { user: authUser } = useAuthContext();
  const router = useRouter();
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
  const [successMsg, setSuccessMsg] = useState("");
  const [actionLoading, setActionLoading] = useState<number | null>(null);
  const [passwordReset, setPasswordReset] = useState<PasswordResetState | null>(null);
  const [confirmDeactivate, setConfirmDeactivate] = useState<AdminUser | null>(null);
  const [editRoles, setEditRoles] = useState<EditRolesState | null>(null);

  const fetchUsers = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/users");
      if (res.ok) {
        const data = await res.json();
        setUsers(data.users);
      } else {
        setError("Failed to load users");
      }
    } catch {
      setError("Network error loading users");
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
      // roles are optional for display
    }
  }, []);

  useEffect(() => {
    Promise.all([fetchUsers(), fetchRoles()]).then(() => setLoading(false));
  }, [fetchUsers, fetchRoles]);

  // Auto-clear success message
  useEffect(() => {
    if (successMsg) {
      const timer = setTimeout(() => setSuccessMsg(""), 3000);
      return () => clearTimeout(timer);
    }
  }, [successMsg]);

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
        setError(data?.error || data?.detail || "Failed to create user");
        return;
      }
      setShowCreate(false);
      setCreateForm({ username: "", email: "", password: "", role_ids: [] });
      setSuccessMsg("User created successfully");
      await fetchUsers();
    } catch {
      setError("Network error");
    }
  }

  async function handleDeactivateConfirmed() {
    if (!confirmDeactivate) return;
    const userId = confirmDeactivate.id;
    setConfirmDeactivate(null);
    setActionLoading(userId);
    try {
      const res = await fetch(`/api/admin/users/${userId}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setError(data?.detail || "Failed to deactivate user");
        return;
      }
      setSuccessMsg("User deactivated");
      await fetchUsers();
    } catch {
      setError("Network error");
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
      setSuccessMsg("User activated");
      await fetchUsers();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleEditRolesSubmit() {
    if (!editRoles) return;
    setActionLoading(editRoles.userId);
    setError("");
    try {
      const res = await fetch(`/api/admin/users/${editRoles.userId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role_ids: editRoles.role_ids }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setError(data?.detail || "Failed to update roles");
        return;
      }
      setEditRoles(null);
      setSuccessMsg(`Roles updated for ${editRoles.username}`);
      await fetchUsers();
    } catch {
      setError("Network error");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleResetPasswordSubmit() {
    if (!passwordReset) return;
    if (passwordReset.password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }
    if (passwordReset.password !== passwordReset.confirm) {
      setError("Passwords do not match");
      return;
    }
    setActionLoading(passwordReset.userId);
    setError("");
    try {
      const res = await fetch(`/api/admin/users/${passwordReset.userId}/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: passwordReset.password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setError(data?.detail || "Failed to reset password");
        return;
      }
      setPasswordReset(null);
      setSuccessMsg(`Password reset for ${passwordReset.username}`);
    } catch {
      setError("Network error");
    } finally {
      setActionLoading(null);
    }
  }

  // Superadmin-only guard
  useEffect(() => {
    if (authUser && !authUser.is_superadmin) {
      router.replace("/admin/usage");
    }
  }, [authUser, router]);

  if (!authUser?.is_superadmin) {
    return <p className="text-muted-foreground p-4">Access denied. Superadmin only.</p>;
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
          className="px-4 py-2 bg-primary text-primary-foreground text-sm rounded-md hover:bg-primary/90"
        >
          {showCreate ? "Cancel" : "Create User"}
        </button>
      </div>

      {/* Success toast */}
      {successMsg && (
        <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-md">
          <p className="text-sm text-green-700">{successMsg}</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md flex justify-between items-center">
          <p className="text-sm text-red-700">{error}</p>
          <button onClick={() => setError("")} className="text-red-400 hover:text-red-600 text-sm ml-2">
            dismiss
          </button>
        </div>
      )}

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
              placeholder="Password (min 8 chars)"
              value={createForm.password}
              onChange={(e) =>
                setCreateForm({ ...createForm, password: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
              required
              minLength={8}
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
          <button
            type="submit"
            className="px-4 py-2 bg-green-600 text-white text-sm rounded-md hover:bg-green-700"
          >
            Create
          </button>
        </form>
      )}

      {/* Password Reset Modal */}
      {passwordReset && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-md shadow-xl">
            <h3 className="text-lg font-semibold mb-4">
              Reset Password: {passwordReset.username}
            </h3>
            <div className="space-y-3">
              <input
                type="password"
                placeholder="New password (min 8 characters)"
                value={passwordReset.password}
                onChange={(e) =>
                  setPasswordReset({ ...passwordReset, password: e.target.value })
                }
                className="w-full px-3 py-2 border rounded-md text-sm"
                autoFocus
                minLength={8}
              />
              <input
                type="password"
                placeholder="Confirm password"
                value={passwordReset.confirm}
                onChange={(e) =>
                  setPasswordReset({ ...passwordReset, confirm: e.target.value })
                }
                className="w-full px-3 py-2 border rounded-md text-sm"
                minLength={8}
              />
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => { setPasswordReset(null); setError(""); }}
                className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200"
              >
                Cancel
              </button>
              <button
                onClick={handleResetPasswordSubmit}
                disabled={actionLoading === passwordReset.userId}
                className="px-4 py-2 text-sm text-primary-foreground bg-primary rounded-md hover:bg-primary/90 disabled:opacity-50"
              >
                Reset Password
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Roles Modal */}
      {editRoles && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-sm shadow-xl">
            <h3 className="text-lg font-semibold mb-4">
              Edit Roles: {editRoles.username}
            </h3>
            <div className="space-y-2 mb-4">
              {roles.map((role) => (
                <label key={role.id} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={editRoles.role_ids.includes(role.id)}
                    onChange={(e) => {
                      const ids = e.target.checked
                        ? [...editRoles.role_ids, role.id]
                        : editRoles.role_ids.filter((id) => id !== role.id);
                      setEditRoles({ ...editRoles, role_ids: ids });
                    }}
                  />
                  {role.name}
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setEditRoles(null); setError(""); }}
                className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200"
              >
                Cancel
              </button>
              <button
                onClick={handleEditRolesSubmit}
                disabled={actionLoading === editRoles.userId}
                className="px-4 py-2 text-sm text-primary-foreground bg-primary rounded-md hover:bg-primary/90 disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Deactivate Confirmation Modal */}
      {confirmDeactivate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-md shadow-xl">
            <h3 className="text-lg font-semibold mb-2">Confirm Deactivation</h3>
            <p className="text-sm text-gray-600 mb-4">
              Are you sure you want to deactivate user <strong>{confirmDeactivate.username}</strong>?
              They will be unable to log in.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmDeactivate(null)}
                className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200"
              >
                Cancel
              </button>
              <button
                onClick={handleDeactivateConfirmed}
                className="px-4 py-2 text-sm text-white bg-red-600 rounded-md hover:bg-red-700"
              >
                Deactivate
              </button>
            </div>
          </div>
        </div>
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
                        className="text-xs bg-primary/10 text-primary px-1.5 py-0.5 rounded"
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
                      onClick={() =>
                        setEditRoles({
                          userId: u.id,
                          username: u.username,
                          role_ids: u.roles.map((r) => r.id),
                        })
                      }
                      disabled={actionLoading === u.id}
                      className="text-xs text-primary hover:text-primary/80 disabled:opacity-50"
                    >
                      Roles
                    </button>
                    <button
                      onClick={() =>
                        setPasswordReset({
                          userId: u.id,
                          username: u.username,
                          password: "",
                          confirm: "",
                        })
                      }
                      disabled={actionLoading === u.id}
                      className="text-xs text-primary hover:text-primary/80 disabled:opacity-50"
                    >
                      Reset PW
                    </button>
                    {u.is_active ? (
                      <button
                        onClick={() => setConfirmDeactivate(u)}
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
