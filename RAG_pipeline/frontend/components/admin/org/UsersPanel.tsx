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
  StatusDot,
  Toast,
} from "@/components/admin/org/shared";
import {
  adminKeys,
  adminMutate,
  useAdminRefresh,
  useAdminRoles,
  useAdminUsers,
  type AdminUser,
} from "@/hooks/useAdminDirectory";

interface CreateForm {
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

const EMPTY_CREATE: CreateForm = { username: "", email: "", password: "", role_ids: [] };

/** Member administration. Owner rows are read-only and protected server-side. */
export function UsersPanel() {
  const usersQuery = useAdminUsers();
  const rolesQuery = useAdminRoles();
  const refresh = useAdminRefresh();

  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateForm>(EMPTY_CREATE);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [passwordReset, setPasswordReset] = useState<PasswordResetState | null>(null);
  const [editRoles, setEditRoles] = useState<EditRolesState | null>(null);
  const [confirmDeactivate, setConfirmDeactivate] = useState<AdminUser | null>(null);

  const users = usersQuery.data ?? [];
  const roles = rolesQuery.data ?? [];

  async function run(work: () => Promise<string>, userId?: number) {
    setError("");
    if (userId) setBusyId(userId);
    try {
      const message = await work();
      await refresh(adminKeys.users);
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
      await adminMutate("/api/admin/users", { method: "POST", body: createForm }, "Failed to create user");
      setShowCreate(false);
      setCreateForm(EMPTY_CREATE);
      return `User "${createForm.username}" created`;
    });
  }

  async function handleSaveRoles() {
    if (!editRoles) return;
    const target = editRoles;
    await run(async () => {
      await adminMutate(
        `/api/admin/users/${target.userId}`,
        { method: "PUT", body: { role_ids: target.role_ids } },
        "Failed to update roles",
      );
      setEditRoles(null);
      return `Roles updated for ${target.username}`;
    }, target.userId);
  }

  async function handleResetPassword() {
    if (!passwordReset) return;
    if (passwordReset.password.length < 12) {
      setError("Password must be at least 12 characters");
      return;
    }
    if (passwordReset.password !== passwordReset.confirm) {
      setError("Passwords do not match");
      return;
    }
    const target = passwordReset;
    await run(async () => {
      await adminMutate(
        `/api/admin/users/${target.userId}/reset-password`,
        { method: "POST", body: { new_password: target.password } },
        "Failed to reset password",
      );
      setPasswordReset(null);
      return `Password reset for ${target.username}`;
    }, target.userId);
  }

  async function handleDeactivate() {
    if (!confirmDeactivate) return;
    const target = confirmDeactivate;
    setConfirmDeactivate(null);
    await run(async () => {
      await adminMutate(`/api/admin/users/${target.id}`, { method: "DELETE" }, "Failed to deactivate user");
      return `${target.username} deactivated`;
    }, target.id);
  }

  async function handleActivate(user: AdminUser) {
    await run(async () => {
      await adminMutate(
        `/api/admin/users/${user.id}`,
        { method: "PUT", body: { is_active: true } },
        "Failed to activate user",
      );
      return `${user.username} activated`;
    }, user.id);
  }

  if (usersQuery.isLoading) return <PanelLoading label="Loading members..." />;

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Members"
        description="Create accounts, adjust role assignments, reset credentials and deactivate access."
        actions={
          <Button size="sm" onClick={() => { setShowCreate((open) => !open); setError(""); }}>
            {showCreate ? "Cancel" : <><Plus className="h-3.5 w-3.5" aria-hidden />Add member</>}
          </Button>
        }
      />

      {usersQuery.isError && <Alert tone="error">{usersQuery.error.message}</Alert>}
      {error && <Alert tone="error" onDismiss={() => setError("")}>{error}</Alert>}
      <Toast message={success} onClear={() => setSuccess("")} />

      {showCreate && (
        <form
          onSubmit={handleCreate}
          className="space-y-4 rounded-xl border border-border bg-muted/20 p-4"
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
              Username
              <Input
                value={createForm.username}
                onChange={(event) => setCreateForm({ ...createForm, username: event.target.value })}
                required
                autoComplete="off"
              />
            </label>
            <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
              Email
              <Input
                type="email"
                value={createForm.email}
                onChange={(event) => setCreateForm({ ...createForm, email: event.target.value })}
                required
                autoComplete="off"
              />
            </label>
            <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
              Password (12-128 chars)
              <Input
                type="password"
                value={createForm.password}
                onChange={(event) => setCreateForm({ ...createForm, password: event.target.value })}
                required
                minLength={12}
                maxLength={128}
                autoComplete="new-password"
              />
            </label>
          </div>
          {roles.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">Roles</p>
              <CheckboxGrid
                items={roles}
                selected={createForm.role_ids}
                onToggle={(id) =>
                  setCreateForm({
                    ...createForm,
                    role_ids: createForm.role_ids.includes(id)
                      ? createForm.role_ids.filter((roleId) => roleId !== id)
                      : [...createForm.role_ids, id],
                  })
                }
                render={(role) => role.name}
                columns={3}
              />
            </div>
          )}
          <Button type="submit" size="sm">Create member</Button>
        </form>
      )}

      <DataTable
        columns={["Member", "Roles", "Status", "Last login", "Actions"]}
        empty="No members yet."
        isEmpty={users.length === 0}
      >
        {users.map((user) => (
          <Row key={user.id}>
            <Cell>
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">{user.username}</span>
                {user.is_superadmin && <Chip tone="owner">Owner</Chip>}
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">{user.email}</p>
            </Cell>
            <Cell>
              <div className="flex flex-wrap gap-1">
                {user.roles.length === 0 && <span className="text-xs text-muted-foreground">none</span>}
                {user.roles.map((role) => (
                  <Chip key={role.id}>{role.name}</Chip>
                ))}
              </div>
            </Cell>
            <Cell>
              <StatusDot active={user.is_active} />
            </Cell>
            <Cell muted className="text-xs">
              {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : "Never"}
            </Cell>
            <ActionCell>
              <InlineAction
                onClick={() =>
                  setEditRoles({
                    userId: user.id,
                    username: user.username,
                    role_ids: user.roles.map((role) => role.id),
                  })
                }
                disabled={busyId === user.id || user.is_superadmin}
                title={user.is_superadmin ? "Owner accounts are outside member administration" : undefined}
              >
                Roles
              </InlineAction>
              <InlineAction
                onClick={() =>
                  setPasswordReset({
                    userId: user.id,
                    username: user.username,
                    password: "",
                    confirm: "",
                  })
                }
                disabled={busyId === user.id || user.is_superadmin}
                title={user.is_superadmin ? "Owner accounts are outside member administration" : undefined}
              >
                Reset password
              </InlineAction>
              {user.is_active ? (
                <InlineAction
                  tone="danger"
                  onClick={() => setConfirmDeactivate(user)}
                  disabled={busyId === user.id || user.is_superadmin}
                  title={user.is_superadmin ? "Cannot deactivate the Owner" : undefined}
                >
                  Deactivate
                </InlineAction>
              ) : (
                <InlineAction
                  tone="success"
                  onClick={() => void handleActivate(user)}
                  disabled={busyId === user.id || user.is_superadmin}
                >
                  Activate
                </InlineAction>
              )}
            </ActionCell>
          </Row>
        ))}
      </DataTable>

      <Modal
        open={editRoles !== null}
        title={`Edit roles: ${editRoles?.username ?? ""}`}
        onClose={() => { setEditRoles(null); setError(""); }}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => { setEditRoles(null); setError(""); }}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => void handleSaveRoles()} isLoading={busyId === editRoles?.userId}>
              Save
            </Button>
          </>
        }
      >
        {editRoles && (
          <CheckboxGrid
            items={roles}
            selected={editRoles.role_ids}
            onToggle={(id) =>
              setEditRoles({
                ...editRoles,
                role_ids: editRoles.role_ids.includes(id)
                  ? editRoles.role_ids.filter((roleId) => roleId !== id)
                  : [...editRoles.role_ids, id],
              })
            }
            render={(role) => role.name}
            columns={1}
          />
        )}
      </Modal>

      <Modal
        open={passwordReset !== null}
        title={`Reset password: ${passwordReset?.username ?? ""}`}
        description="The member is not notified automatically — share the new password over a trusted channel."
        onClose={() => { setPasswordReset(null); setError(""); }}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => { setPasswordReset(null); setError(""); }}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => void handleResetPassword()}
              isLoading={busyId === passwordReset?.userId}
            >
              Reset password
            </Button>
          </>
        }
      >
        {passwordReset && (
          <>
            <Input
              type="password"
              placeholder="New password (12-128 characters)"
              value={passwordReset.password}
              onChange={(event) => setPasswordReset({ ...passwordReset, password: event.target.value })}
              autoFocus
              minLength={12}
              maxLength={128}
              autoComplete="new-password"
            />
            <Input
              type="password"
              placeholder="Confirm password"
              value={passwordReset.confirm}
              onChange={(event) => setPasswordReset({ ...passwordReset, confirm: event.target.value })}
              minLength={12}
              maxLength={128}
              autoComplete="new-password"
            />
            {error && <Alert tone="error">{error}</Alert>}
          </>
        )}
      </Modal>

      <Modal
        open={confirmDeactivate !== null}
        title="Deactivate member"
        description={`${confirmDeactivate?.username ?? ""} will no longer be able to sign in. The account and its history are kept.`}
        onClose={() => setConfirmDeactivate(null)}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setConfirmDeactivate(null)}>
              Cancel
            </Button>
            <Button variant="destructive" size="sm" onClick={() => void handleDeactivate()}>
              Deactivate
            </Button>
          </>
        }
      >
        <span className="sr-only">Confirm deactivation</span>
      </Modal>
    </div>
  );
}
