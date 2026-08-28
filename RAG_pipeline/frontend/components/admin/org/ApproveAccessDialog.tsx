"use client";

import { useMemo, useState } from "react";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { cn } from "@/lib/utils";
import { usePermissions } from "@/hooks/usePermissions";
import {
  useAdminProjects,
  useAdminRoles,
  type AdminAccessRequest,
} from "@/hooks/useAdminDirectory";

export interface ApprovalDecision {
  role_id: number | null;
  project_ids: string[];
  welcome_note: string | null;
}

interface Props {
  request: AdminAccessRequest | null;
  isBusy: boolean;
  onClose: () => void;
  onApprove: (decision: ApprovalDecision) => void;
}

/**
 * Decide role and project scope at the moment access is approved.
 *
 * Approval used to be a single "Approve" button that sent an invite carrying no
 * authority, so every new member landed as a plain developer and had to be
 * promoted afterwards from a different screen. Making the role an explicit,
 * required choice here means the decision is made by the person reviewing the
 * request, while they still have its context in front of them.
 *
 * Roles the deployment treats as Owner authority are not offered: the backend
 * refuses them, and showing an option that always fails is worse than omitting
 * it.
 */
const RESERVED_OWNER_ROLE_NAMES = new Set(["owner", "superadmin"]);

/** Roles whose name signals member administration, for the warning callout. */
const ELEVATED_ROLE_NAMES = new Set(["admin", "administrator", "manager"]);

export function ApproveAccessDialog({
  request,
  isBusy,
  onClose,
  onApprove,
}: Props) {
  const { canAssignProjects } = usePermissions();
  const rolesQuery = useAdminRoles(Boolean(request));
  const projectsQuery = useAdminProjects(Boolean(request) && canAssignProjects);

  const [roleId, setRoleId] = useState<number | null>(null);
  const [projectIds, setProjectIds] = useState<string[]>([]);
  const [note, setNote] = useState("");

  const roles = useMemo(
    () =>
      (rolesQuery.data ?? []).filter(
        (role) => !RESERVED_OWNER_ROLE_NAMES.has(role.name.trim().toLowerCase()),
      ),
    [rolesQuery.data],
  );

  const selectedRole = roles.find((role) => role.id === roleId) ?? null;
  const grantsAdministration =
    selectedRole !== null &&
    (ELEVATED_ROLE_NAMES.has(selectedRole.name.trim().toLowerCase()) ||
      // Permission-derived rather than name-derived, so a custom role that
      // carries member administration still triggers the warning.
      (selectedRole.permissions ?? []).some((permission) =>
        ["manage_users", "manage_roles", "assign_projects"].includes(
          permission.codename,
        ),
      ));

  function reset() {
    setRoleId(null);
    setProjectIds([]);
    setNote("");
  }

  function submit() {
    onApprove({
      role_id: roleId,
      project_ids: projectIds,
      welcome_note: note.trim() || null,
    });
    reset();
  }

  function close() {
    reset();
    onClose();
  }

  return (
    <Modal
      open={Boolean(request)}
      title="Approve access"
      description={
        request
          ? `${request.full_name || request.email} will receive an invitation valid for 48 hours.`
          : undefined
      }
      onClose={close}
      size="lg"
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="md" onClick={close} disabled={isBusy}>
            Cancel
          </Button>
          <Button
            size="md"
            onClick={submit}
            isLoading={isBusy}
            disabled={roleId === null}
          >
            Send invitation
          </Button>
        </div>
      }
    >
      <div className="space-y-5">
        <fieldset>
          <legend className="text-sm font-medium text-foreground">
            Role on joining
          </legend>
          <p className="mt-1 text-xs text-muted-foreground">
            This is what the member arrives with. Changing it later is possible
            but means someone has to remember to do it.
          </p>

          {rolesQuery.isLoading && (
            <p className="mt-3 text-sm text-muted-foreground">Loading roles...</p>
          )}
          {rolesQuery.isError && (
            <p className="mt-3 text-sm text-destructive">
              {rolesQuery.error.message}
            </p>
          )}

          <div className="mt-3 space-y-2">
            {roles.map((role) => {
              const active = role.id === roleId;
              return (
                <label
                  key={role.id}
                  className={cn(
                    "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors",
                    active
                      ? "border-primary/50 bg-primary/[0.06]"
                      : "border-border hover:bg-muted/40",
                  )}
                >
                  <input
                    type="radio"
                    name="approve-role"
                    className="mt-1 accent-[hsl(var(--primary))]"
                    checked={active}
                    onChange={() => setRoleId(role.id)}
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium capitalize text-foreground">
                      {role.name}
                    </span>
                    {role.description && (
                      <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
                        {role.description}
                      </span>
                    )}
                  </span>
                </label>
              );
            })}
          </div>

          {grantsAdministration && (
            <div className="mt-3 flex gap-3 rounded-lg border border-amber-300/60 bg-amber-50/70 p-3 dark:border-amber-500/25 dark:bg-amber-500/[0.07]">
              <ShieldAlert
                className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-400"
                aria-hidden
              />
              <p className="text-xs leading-5 text-foreground">
                <span className="font-semibold">This grants administration.</span>{" "}
                They will be able to approve access, set other members&rsquo;
                roles and operate projects. The grant is recorded in the audit
                log against your account.
              </p>
            </div>
          )}
          {selectedRole && !grantsAdministration && (
            <div className="mt-3 flex gap-3 rounded-lg border border-border bg-muted/30 p-3">
              <ShieldCheck
                className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
                aria-hidden
              />
              <p className="text-xs leading-5 text-muted-foreground">
                Standard member access. They will see only the projects they are
                assigned to.
              </p>
            </div>
          )}
        </fieldset>

        {canAssignProjects && (
          <fieldset>
            <legend className="text-sm font-medium text-foreground">
              Projects <span className="text-muted-foreground">(optional)</span>
            </legend>
            <p className="mt-1 text-xs text-muted-foreground">
              Granted on acceptance as membership, never ownership. Leave empty
              to assign later.
            </p>
            {projectsQuery.data && projectsQuery.data.length > 0 ? (
              <div className="mt-3 max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
                {projectsQuery.data.map((project) => (
                  <label
                    key={project.id}
                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted/50"
                  >
                    <input
                      type="checkbox"
                      className="accent-[hsl(var(--primary))]"
                      checked={projectIds.includes(project.id)}
                      onChange={(event) =>
                        setProjectIds((prev) =>
                          event.target.checked
                            ? [...prev, project.id]
                            : prev.filter((id) => id !== project.id),
                        )
                      }
                    />
                    <span className="truncate text-foreground">
                      {project.display_name || project.id}
                    </span>
                  </label>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-xs text-muted-foreground">
                No projects yet — assign access after one exists.
              </p>
            )}
          </fieldset>
        )}

        <div>
          <label
            htmlFor="approve-welcome-note"
            className="text-sm font-medium text-foreground"
          >
            Welcome note <span className="text-muted-foreground">(optional)</span>
          </label>
          <p className="mt-1 text-xs text-muted-foreground">
            Shown on their first screen, alongside your name.
          </p>
          <Input
            id="approve-welcome-note"
            className="mt-2"
            value={note}
            maxLength={1000}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Joining the integration team — start with the Werkorder docs."
          />
        </div>
      </div>
    </Modal>
  );
}
