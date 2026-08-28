"use client";

import { useState, type FormEvent } from "react";
import { MailCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SelectMenu } from "@/components/ui/select-menu";
import { usePermissions } from "@/hooks/usePermissions";
import {
  ActionCell,
  Alert,
  Cell,
  Chip,
  DataTable,
  InlineAction,
  PanelHeader,
  PanelLoading,
  Row,
  Toast,
} from "@/components/admin/org/shared";
import {
  adminKeys,
  adminMutate,
  useAdminAccessRequests,
  useAdminRefresh,
  type AdminAccessRequest,
} from "@/hooks/useAdminDirectory";
import {
  ApproveAccessDialog,
  type ApprovalDecision,
} from "./ApproveAccessDialog";

const STATUS_OPTIONS = [
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "", label: "All statuses" },
];

const STATUS_TONE: Record<string, "default" | "muted" | "owner"> = {
  pending: "owner",
  approved: "default",
  rejected: "muted",
};

/** Access requests and outstanding invitations for this deployment. */
export function AccessPanel() {
  const { isOwner } = usePermissions();
  const [status, setStatus] = useState("pending");
  const accessQuery = useAdminAccessRequests(status);
  const refresh = useAdminRefresh();

  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [testEmail, setTestEmail] = useState("");
  /** The request being approved; approval now needs a role decision first. */
  const [approving, setApproving] = useState<AdminAccessRequest | null>(null);

  const requests = accessQuery.data?.requests ?? [];
  const invites = accessQuery.data?.invites ?? [];

  /** Returns true when the action succeeded, so callers can close their UI. */
  async function act(key: string, url: string, body?: unknown): Promise<boolean> {
    setBusyKey(key);
    setError("");
    setNotice("");
    try {
      const data = await adminMutate<{ message?: string; setup_url?: string; invite_link?: string }>(
        url,
        { method: "POST", body },
        "Action failed.",
      );
      const setupHint = data?.setup_url || data?.invite_link;
      setNotice(setupHint ? `${data?.message ?? "Done"}: ${setupHint}` : data?.message ?? "Done.");
      await refresh(["admin", "access-requests"], adminKeys.users);
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Action failed.");
      return false;
    } finally {
      setBusyKey(null);
    }
  }

  function handleTestEmail(event: FormEvent) {
    event.preventDefault();
    void act("test-email", "/api/admin/email/test", { to: testEmail });
  }

  async function handleApprove(decision: ApprovalDecision) {
    const target = approving;
    if (!target) return;
    // Close on success only. A failed approval keeps the dialog open so the
    // reviewer does not have to re-make the role decision.
    const ok = await act(
      `approve-${target.id}`,
      `/api/admin/access-requests/${target.id}/approve`,
      decision,
    );
    if (ok) setApproving(null);
  }

  return (
    <div className="space-y-4">
      <ApproveAccessDialog
        request={approving}
        isBusy={busyKey === `approve-${approving?.id}`}
        onClose={() => setApproving(null)}
        onApprove={(decision) => void handleApprove(decision)}
      />

      <PanelHeader
        title="Access requests"
        description="Approve or reject company-local app access, and manage outstanding invitations."
        actions={
          <SelectMenu
            value={status}
            onChange={setStatus}
            options={STATUS_OPTIONS}
            placeholder="All statuses"
            ariaLabel="Filter by request status"
            variant="field"
            size="md"
            align="end"
          />
        }
      />

      {accessQuery.isError && <Alert tone="error">{accessQuery.error.message}</Alert>}
      {error && <Alert tone="error" onDismiss={() => setError("")}>{error}</Alert>}
      <Toast message={notice} onClear={() => setNotice("")} />

      {accessQuery.isLoading ? (
        <PanelLoading label="Loading access requests..." />
      ) : (
        <DataTable
          columns={["Requester", "Status", "Requested", "Actions"]}
          empty="No access requests for this filter."
          isEmpty={requests.length === 0}
        >
          {requests.map((request) => (
            <Row key={request.id}>
              <Cell>
                <span className="font-medium text-foreground">
                  {request.full_name || request.email}
                </span>
                <p className="mt-0.5 text-xs text-muted-foreground">{request.email}</p>
              </Cell>
              <Cell>
                <Chip tone={STATUS_TONE[request.status] ?? "muted"}>{request.status}</Chip>
                {/* Show what authority was granted. A reviewer needs to be able
                    to spot an accidental admin grant while the invite is still
                    pending, without opening anything. */}
                {request.invite?.assigned_role_name && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    as <span className="font-medium capitalize text-foreground">
                      {request.invite.assigned_role_name}
                    </span>
                  </p>
                )}
                {request.rejection_reason && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{request.rejection_reason}</p>
                )}
              </Cell>
              <Cell muted className="text-xs">
                {new Date(request.created_at).toLocaleString()}
              </Cell>
              <ActionCell>
                {request.status === "pending" && (
                  <>
                    <InlineAction
                      tone="success"
                      onClick={() => setApproving(request)}
                      disabled={busyKey === `approve-${request.id}`}
                    >
                      Approve...
                    </InlineAction>
                    <InlineAction
                      tone="danger"
                      onClick={() =>
                        void act(`reject-${request.id}`, `/api/admin/access-requests/${request.id}/reject`, {
                          reason: "Rejected by admin",
                        })
                      }
                      disabled={busyKey === `reject-${request.id}`}
                    >
                      Reject
                    </InlineAction>
                  </>
                )}
                {request.invite && request.invite.status === "pending" && (
                  <>
                    <InlineAction
                      onClick={() => void act(`resend-${request.invite!.id}`, `/api/admin/invites/${request.invite!.id}/resend`)}
                      disabled={busyKey === `resend-${request.invite.id}`}
                    >
                      Resend
                    </InlineAction>
                    <InlineAction
                      tone="danger"
                      onClick={() => void act(`revoke-${request.invite!.id}`, `/api/admin/invites/${request.invite!.id}/revoke`)}
                      disabled={busyKey === `revoke-${request.invite.id}`}
                    >
                      Revoke
                    </InlineAction>
                  </>
                )}
              </ActionCell>
            </Row>
          ))}
        </DataTable>
      )}

      {invites.length > 0 && (
        <div className="rounded-xl border border-border bg-card p-4 shadow-elevation-sm">
          <h3 className="text-sm font-semibold text-foreground">Pending invitations</h3>
          <ul className="mt-3 divide-y divide-border">
            {invites.map((invite) => (
              <li key={invite.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span className="min-w-0">
                  <span className="font-medium text-foreground">{invite.email}</span>
                  {invite.assigned_role_name && (
                    <span className="ml-2 text-xs capitalize text-muted-foreground">
                      · {invite.assigned_role_name}
                    </span>
                  )}
                  <span className="ml-2 text-xs text-muted-foreground">
                    expires {new Date(invite.expires_at).toLocaleString()}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-1">
                  <InlineAction
                    onClick={() => void act(`resend-${invite.id}`, `/api/admin/invites/${invite.id}/resend`)}
                    disabled={busyKey === `resend-${invite.id}`}
                  >
                    Resend
                  </InlineAction>
                  <InlineAction
                    tone="danger"
                    onClick={() => void act(`revoke-${invite.id}`, `/api/admin/invites/${invite.id}/revoke`)}
                    disabled={busyKey === `revoke-${invite.id}`}
                  >
                    Revoke
                  </InlineAction>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {isOwner && (
        <form
          onSubmit={handleTestEmail}
          className="rounded-xl border border-border bg-muted/20 p-4"
        >
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <MailCheck className="h-4 w-4 text-muted-foreground" aria-hidden />
            Verify invitation delivery
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Send a test message before assuming a member never received their invite.
          </p>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <Input
              type="email"
              value={testEmail}
              onChange={(event) => setTestEmail(event.target.value)}
              placeholder="Test email recipient"
              required
              className="sm:max-w-sm"
            />
            <Button type="submit" variant="secondary" size="md" isLoading={busyKey === "test-email"}>
              Send test
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
