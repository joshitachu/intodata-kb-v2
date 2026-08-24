"use client";

import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ActionCell,
  Alert,
  Cell,
  CheckboxGrid,
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
  useAdminPermissions,
  useAdminRefresh,
  useAdminRoles,
  type AdminRole,
} from "@/hooks/useAdminDirectory";

interface RoleForm {
  name: string;
  description: string;
  permission_ids: number[];
}

const EMPTY_ROLE: RoleForm = { name: "", description: "", permission_ids: [] };

/** Owner-only role definitions and their permission grants. */
export function RolesPanel() {
  const rolesQuery = useAdminRoles();
  const permissionsQuery = useAdminPermissions();
  const refresh = useAdminRefresh();

  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<RoleForm>(EMPTY_ROLE);
  const [editRole, setEditRole] = useState<(RoleForm & { roleId: number }) | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminRole | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  const roles = rolesQuery.data ?? [];
  const permissions = permissionsQuery.data ?? [];

  async function run(work: () => Promise<string>, roleId?: number) {
    setError("");
    if (roleId) setBusyId(roleId);
    try {
      const message = await work();
      // Role changes alter what every member can do, so refresh users too.
      await refresh(adminKeys.roles, adminKeys.users);
      setSuccess(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Request failed");
    } finally {
      setBusyId(null);
    }
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      await adminMutate("/api/admin/roles", { method: "POST", body: createForm }, "Failed to create role");
      setShowCreate(false);
      setCreateForm(EMPTY_ROLE);
      return `Role "${createForm.name}" created`;
    });
  }

  async function handleEdit(event: FormEvent) {
    event.preventDefault();
    if (!editRole) return;
    const target = editRole;
    await run(async () => {
      await adminMutate(
        `/api/admin/roles/${target.roleId}`,
        {
          method: "PUT",
          body: {
            name: target.name,
            description: target.description || null,
            permission_ids: target.permission_ids,
          },
        },
        "Failed to update role",
      );
      setEditRole(null);
      return `Role "${target.name}" updated`;
    }, target.roleId);
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeleteTarget(null);
    await run(async () => {
      await adminMutate(`/api/admin/roles/${target.id}`, { method: "DELETE" }, "Failed to delete role");
      return `Role "${target.name}" deleted`;
    }, target.id);
  }

  function togglePermission(form: RoleForm, id: number): number[] {
    return form.permission_ids.includes(id)
      ? form.permission_ids.filter((permId) => permId !== id)
      : [...form.permission_ids, id];
  }

  if (rolesQuery.isLoading) return <PanelLoading label="Loading roles..." />;

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Roles"
        description="Define which permissions a role grants. Changes apply immediately to every member holding it."
        actions={
          <Button
            size="sm"
            onClick={() => {
              setShowCreate((open) => !open);
              setEditRole(null);
              setError("");
            }}
          >
            {showCreate ? "Cancel" : <><Plus className="h-3.5 w-3.5" aria-hidden />New role</>}
          </Button>
        }
      />

      {rolesQuery.isError && <Alert tone="error">{rolesQuery.error.message}</Alert>}
      {error && <Alert tone="error" onDismiss={() => setError("")}>{error}</Alert>}
      <Toast message={success} onClear={() => setSuccess("")} />

      {showCreate && (
        <form onSubmit={handleCreate} className="space-y-4 rounded-xl border border-border bg-muted/20 p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
              Role name
              <Input
                value={createForm.name}
                onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })}
                required
              />
            </label>
            <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
              Description (optional)
              <Input
                value={createForm.description}
                onChange={(event) => setCreateForm({ ...createForm, description: event.target.value })}
              />
            </label>
          </div>
          {permissions.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">Permissions</p>
              <CheckboxGrid
                items={permissions}
                selected={createForm.permission_ids}
                onToggle={(id) =>
                  setCreateForm({ ...createForm, permission_ids: togglePermission(createForm, id) })
                }
                render={(perm) => (
                  <span title={perm.description || undefined} className="font-mono text-xs">
                    {perm.codename}
                  </span>
                )}
                columns={3}
              />
            </div>
          )}
          <Button type="submit" size="sm">Create role</Button>
        </form>
      )}

      <DataTable
        columns={["Role", "Permissions", "Actions"]}
        empty="No roles defined."
        isEmpty={roles.length === 0}
      >
        {roles.map((role) => (
          <Row key={role.id}>
            <Cell>
              <span className="font-medium text-foreground">{role.name}</span>
              <p className="mt-0.5 text-xs text-muted-foreground">{role.description || "No description"}</p>
            </Cell>
            <Cell>
              <div className="flex max-w-xl flex-wrap gap-1">
                {role.permissions.length === 0 && (
                  <span className="text-xs text-muted-foreground">none</span>
                )}
                {role.permissions.map((perm) => (
                  <Chip key={perm.id} tone="permission" title={perm.description || undefined}>
                    {perm.codename}
                  </Chip>
                ))}
              </div>
            </Cell>
            <ActionCell>
              <InlineAction
                onClick={() =>
                  setEditRole({
                    roleId: role.id,
                    name: role.name,
                    description: role.description ?? "",
                    permission_ids: role.permissions.map((perm) => perm.id),
                  })
                }
                disabled={busyId === role.id}
              >
                Edit
              </InlineAction>
              {role.name !== "admin" && (
                <InlineAction tone="danger" onClick={() => setDeleteTarget(role)} disabled={busyId === role.id}>
                  Delete
                </InlineAction>
              )}
            </ActionCell>
          </Row>
        ))}
      </DataTable>

      <Modal
        open={editRole !== null}
        title={`Edit role: ${editRole?.name ?? ""}`}
        onClose={() => { setEditRole(null); setError(""); }}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => { setEditRole(null); setError(""); }}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={(event) => void handleEdit(event as unknown as FormEvent)}
              isLoading={busyId === editRole?.roleId}
            >
              Save changes
            </Button>
          </>
        }
      >
        {editRole && (
          <>
            <label className="block space-y-1.5 text-xs font-medium text-muted-foreground">
              Role name
              <Input
                value={editRole.name}
                onChange={(event) => setEditRole({ ...editRole, name: event.target.value })}
              />
            </label>
            <label className="block space-y-1.5 text-xs font-medium text-muted-foreground">
              Description
              <Input
                value={editRole.description}
                onChange={(event) => setEditRole({ ...editRole, description: event.target.value })}
              />
            </label>
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">Permissions</p>
              <div className="max-h-56 overflow-y-auto pr-1">
                <CheckboxGrid
                  items={permissions}
                  selected={editRole.permission_ids}
                  onToggle={(id) =>
                    setEditRole({ ...editRole, permission_ids: togglePermission(editRole, id) })
                  }
                  render={(perm) => (
                    <span title={perm.description || undefined} className="font-mono text-xs">
                      {perm.codename}
                    </span>
                  )}
                  columns={1}
                />
              </div>
            </div>
          </>
        )}
      </Modal>

      <Modal
        open={deleteTarget !== null}
        title="Delete role"
        description={`Members holding "${deleteTarget?.name ?? ""}" lose every permission it grants. This cannot be undone.`}
        onClose={() => setDeleteTarget(null)}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" size="sm" onClick={() => void handleDelete()}>
              Delete role
            </Button>
          </>
        }
      >
        <span className="sr-only">Confirm role deletion</span>
      </Modal>
    </div>
  );
}
