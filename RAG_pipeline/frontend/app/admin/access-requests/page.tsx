"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthContext } from "@/components/AuthProvider";

interface Invite {
  id: number;
  email: string;
  status: string;
  expires_at: string;
  accepted_user_id: number | null;
}

interface AccessRequest {
  id: number;
  email: string;
  full_name: string | null;
  status: string;
  rejection_reason: string | null;
  created_at: string;
  invite: Invite | null;
}

export default function AccessRequestsPage() {
  const router = useRouter();
  const { user } = useAuthContext();
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [status, setStatus] = useState("pending");
  const [error, setError] = useState("");
  const [actionId, setActionId] = useState<string | null>(null);
  const [testEmail, setTestEmail] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (user && !user.is_superadmin) router.replace("/admin/usage");
  }, [user, router]);

  async function load() {
    setError("");
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    const res = await fetch(`/api/admin/access-requests${qs}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.error || "Failed to load access requests.");
      return;
    }
    setRequests(data.requests || []);
    setInvites(data.invites || []);
  }

  useEffect(() => {
    if (user?.is_superadmin) void load();
  }, [status, user?.is_superadmin]);

  async function act(key: string, url: string, body?: unknown) {
    setActionId(key);
    setMessage("");
    setError("");
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Action failed.");
        return;
      }
      const setupHint = data.setup_url || data.invite_link;
      setMessage(setupHint ? `${data.message}: ${setupHint}` : data.message || "Done.");
      await load();
    } finally {
      setActionId(null);
    }
  }

  if (!user?.is_superadmin) {
    return <p className="text-muted-foreground p-4">Access denied. Superadmin only.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Access Requests</h1>
          <p className="text-sm text-gray-500">Company-local app access for this deployment.</p>
        </div>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="border border-gray-300 rounded-md px-3 py-2 text-sm"
        >
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="">All</option>
        </select>
      </div>

      {error && <div className="text-sm text-red-600 bg-red-50 p-3 rounded-md">{error}</div>}
      {message && <div className="text-sm text-gray-700 bg-gray-50 p-3 rounded-md">{message}</div>}

      <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th className="p-3">Requester</th>
              <th className="p-3">Status</th>
              <th className="p-3">Created</th>
              <th className="p-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {requests.map((row) => (
              <tr key={row.id} className="border-t border-gray-100">
                <td className="p-3">
                  <div className="font-medium text-gray-900">{row.full_name || row.email}</div>
                  <div className="text-gray-500">{row.email}</div>
                </td>
                <td className="p-3">{row.status}</td>
                <td className="p-3">{new Date(row.created_at).toLocaleString()}</td>
                <td className="p-3">
                  <div className="flex justify-end gap-2">
                    {row.status === "pending" && (
                      <>
                        <button
                          onClick={() => act(`approve-${row.id}`, `/api/admin/access-requests/${row.id}/approve`)}
                          disabled={actionId === `approve-${row.id}`}
                          className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground disabled:opacity-50"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => act(`reject-${row.id}`, `/api/admin/access-requests/${row.id}/reject`, { reason: "Rejected by admin" })}
                          disabled={actionId === `reject-${row.id}`}
                          className="px-3 py-1.5 rounded-md border border-gray-300 disabled:opacity-50"
                        >
                          Reject
                        </button>
                      </>
                    )}
                    {row.invite && row.invite.status === "pending" && (
                      <>
                        <button
                          onClick={() => act(`resend-${row.invite!.id}`, `/api/admin/invites/${row.invite!.id}/resend`)}
                          className="px-3 py-1.5 rounded-md border border-gray-300"
                        >
                          Resend
                        </button>
                        <button
                          onClick={() => act(`revoke-${row.invite!.id}`, `/api/admin/invites/${row.invite!.id}/revoke`)}
                          className="px-3 py-1.5 rounded-md border border-gray-300"
                        >
                          Revoke
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {requests.length === 0 && (
              <tr>
                <td className="p-6 text-center text-gray-500" colSpan={4}>No access requests.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {invites.length > 0 && (
        <div className="border border-gray-200 rounded-lg p-4 bg-white">
          <h2 className="font-semibold text-gray-900">Pending invites</h2>
          <div className="mt-3 space-y-2">
            {invites.map((invite) => (
              <div key={invite.id} className="flex items-center justify-between gap-3 text-sm">
                <span>{invite.email} expires {new Date(invite.expires_at).toLocaleString()}</span>
                <div className="flex gap-2">
                  <button onClick={() => act(`resend-${invite.id}`, `/api/admin/invites/${invite.id}/resend`)} className="px-3 py-1.5 rounded-md border border-gray-300">Resend</button>
                  <button onClick={() => act(`revoke-${invite.id}`, `/api/admin/invites/${invite.id}/revoke`)} className="px-3 py-1.5 rounded-md border border-gray-300">Revoke</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void act("test-email", "/api/admin/email/test", { to: testEmail });
        }}
        className="border border-gray-200 rounded-lg p-4 bg-white flex gap-3"
      >
        <input
          type="email"
          value={testEmail}
          onChange={(e) => setTestEmail(e.target.value)}
          placeholder="Test email recipient"
          className="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm"
          required
        />
        <button className="px-4 py-2 rounded-md border border-gray-300 text-sm" disabled={actionId === "test-email"}>
          Send test
        </button>
      </form>
    </div>
  );
}
