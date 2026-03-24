"use client";

import { useCallback, useEffect, useState } from "react";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface PermissionItem {
  id: number;
  codename: string;
  description: string | null;
}

interface RoleItem {
  id: number;
  name: string;
  description: string | null;
  permissions: PermissionItem[];
}

interface CreateRoleForm {
  name: string;
  description: string;
  permission_ids: number[];
}

interface EditState {
  roleId: number;
  name: string;
  description: string;
  permission_ids: number[];
}

export default function AdminRolesPage() {
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [permissions, setPermissions] = useState<PermissionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateRoleForm>({
    name: "",
    description: "",
    permission_ids: [],
  });
  const [editState, setEditState] = useState<EditState | null>(null);
  const [error, setError] = useState("");
  const [actionLoading, setActionLoading] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<RoleItem | null>(null);

  const fetchRoles = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/roles");
      if (res.ok) {
        const data: { roles: RoleItem[] } = await res.json();
        setRoles(data.roles);
      }
    } catch {
      // ignore
    }
  }, []);

  const fetchPermissions = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/permissions");
      if (res.ok) {
        const data: { permissions: PermissionItem[] } = await res.json();
        setPermissions(data.permissions);
      }
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    Promise.all([fetchRoles(), fetchPermissions()]).then(() => setLoading(false));
  }, [fetchRoles, fetchPermissions]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await fetch("/api/admin/roles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(createForm),
      });
      if (!res.ok) {
        const data: { error?: string } | null = await res.json().catch(() => null);
        setError(data?.error || "Failed to create role");
        return;
      }
      setShowCreate(false);
      setCreateForm({ name: "", description: "", permission_ids: [] });
      await fetchRoles();
    } catch {
      setError("Network error");
    }
  }

  async function handleEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editState) return;
    setError("");
    setActionLoading(editState.roleId);
    try {
      const res = await fetch(`/api/admin/roles/${editState.roleId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: editState.name,
          description: editState.description || null,
          permission_ids: editState.permission_ids,
        }),
      });
      if (!res.ok) {
        const data: { error?: string } | null = await res.json().catch(() => null);
        setError(data?.error || "Failed to update role");
        return;
      }
      setEditState(null);
      await fetchRoles();
    } catch {
      setError("Network error");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDeleteConfirmed() {
    if (!deleteTarget) return;
    const roleId = deleteTarget.id;
    setDeleteTarget(null);
    setActionLoading(roleId);
    try {
      const res = await fetch(`/api/admin/roles/${roleId}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const data: { error?: string } | null = await res.json().catch(() => null);
        setError(data?.error || "Failed to delete role");
        return;
      }
      await fetchRoles();
    } catch {
      setError("Network error");
    } finally {
      setActionLoading(null);
    }
  }

  function startEdit(role: RoleItem) {
    setEditState({
      roleId: role.id,
      name: role.name,
      description: role.description || "",
      permission_ids: role.permissions.map((p) => p.id),
    });
    setError("");
  }

  function togglePermission(ids: number[], permId: number): number[] {
    return ids.includes(permId)
      ? ids.filter((id) => id !== permId)
      : [...ids, permId];
  }

  if (loading) {
    return <p className="text-muted-foreground p-4">Loading roles...</p>;
  }

  return (
    <div className="max-w-5xl mx-auto py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Role Management</h1>
        <button
          onClick={() => {
            setShowCreate(!showCreate);
            setEditState(null);
            setError("");
          }}
          className="px-4 py-2 bg-[#001e4d] text-white text-sm rounded-md hover:bg-[#001e4d]/90"
        >
          {showCreate ? "Cancel" : "Create Role"}
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {showCreate && (
        <form
          onSubmit={handleCreate}
          className="bg-white border rounded-lg p-4 mb-6 space-y-3"
        >
          <h3 className="font-medium text-sm text-gray-700">New Role</h3>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="text"
              placeholder="Role name"
              value={createForm.name}
              onChange={(e) =>
                setCreateForm({ ...createForm, name: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
              required
            />
            <input
              type="text"
              placeholder="Description (optional)"
              value={createForm.description}
              onChange={(e) =>
                setCreateForm({ ...createForm, description: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
            />
          </div>
          {permissions.length > 0 && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Permissions:</p>
              <div className="flex gap-2 flex-wrap">
                {permissions.map((perm) => (
                  <label
                    key={perm.id}
                    className="flex items-center gap-1 text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={createForm.permission_ids.includes(perm.id)}
                      onChange={() =>
                        setCreateForm({
                          ...createForm,
                          permission_ids: togglePermission(
                            createForm.permission_ids,
                            perm.id,
                          ),
                        })
                      }
                    />
                    <span title={perm.description || undefined}>
                      {perm.codename}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <button
            type="submit"
            className="px-4 py-2 bg-[#a0e06f] text-[#060e1e] text-sm rounded-md hover:bg-[#a0e06f]/90"
          >
            Create
          </button>
        </form>
      )}

      {/* Edit modal */}
      {editState && (
        <form
          onSubmit={handleEdit}
          className="bg-white border-2 border-[#fe9543]/40 rounded-lg p-4 mb-6 space-y-3"
        >
          <h3 className="font-medium text-sm text-gray-700">
            Editing Role: {editState.name}
          </h3>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="text"
              placeholder="Role name"
              value={editState.name}
              onChange={(e) =>
                setEditState({ ...editState, name: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
              required
            />
            <input
              type="text"
              placeholder="Description (optional)"
              value={editState.description}
              onChange={(e) =>
                setEditState({ ...editState, description: e.target.value })
              }
              className="px-3 py-2 border rounded-md text-sm"
            />
          </div>
          {permissions.length > 0 && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Permissions:</p>
              <div className="flex gap-2 flex-wrap">
                {permissions.map((perm) => (
                  <label
                    key={perm.id}
                    className="flex items-center gap-1 text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={editState.permission_ids.includes(perm.id)}
                      onChange={() =>
                        setEditState({
                          ...editState,
                          permission_ids: togglePermission(
                            editState.permission_ids,
                            perm.id,
                          ),
                        })
                      }
                    />
                    <span title={perm.description || undefined}>
                      {perm.codename}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={actionLoading === editState.roleId}
              className="px-4 py-2 bg-[#001e4d] text-white text-sm rounded-md hover:bg-[#001e4d]/90 disabled:opacity-50"
            >
              Save Changes
            </button>
            <button
              type="button"
              onClick={() => setEditState(null)}
              className="px-4 py-2 bg-gray-200 text-gray-700 text-sm rounded-md hover:bg-gray-300"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="bg-white border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                ID
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Name
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Description
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Permissions
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {roles.map((role) => (
              <tr key={role.id} className="border-b last:border-0">
                <td className="px-4 py-3 text-gray-500">{role.id}</td>
                <td className="px-4 py-3 font-medium">{role.name}</td>
                <td className="px-4 py-3 text-gray-600">
                  {role.description || "-"}
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-1 flex-wrap">
                    {role.permissions.length === 0 && (
                      <span className="text-xs text-gray-400">none</span>
                    )}
                    {role.permissions.map((p) => (
                      <span
                        key={p.id}
                        className="text-xs bg-[#6370b0]/10 text-[#6370b0] px-1.5 py-0.5 rounded"
                        title={p.description || undefined}
                      >
                        {p.codename}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-2">
                    <button
                      onClick={() => startEdit(role)}
                      disabled={actionLoading === role.id}
                      className="text-xs text-[#fe9543] hover:text-[#fe9543]/80 disabled:opacity-50"
                    >
                      Edit
                    </button>
                    {role.name !== "admin" && (
                      <button
                        onClick={() => setDeleteTarget(role)}
                        disabled={actionLoading === role.id}
                        className="text-xs text-red-600 hover:text-red-800 disabled:opacity-50"
                      >
                        Delete
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {roles.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-6 text-center text-gray-400"
                >
                  No roles found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Role"
        message={`Are you sure you want to delete the role "${deleteTarget?.name}"? Users with this role will lose its permissions.`}
        confirmLabel="Delete"
        onConfirm={handleDeleteConfirmed}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
