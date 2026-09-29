import { useState } from "react";
import type React from "react";
import { describeMcError, type FleetClient } from "../api/fleet";
import {
  useDeleteFleetClient,
  useRotateTelemetryKey,
  useUpdateFleetClient,
  type OneTimeClientKey,
} from "../hooks/useFleetClients";
import { instanceUrlProblem } from "../lib/fleetClient";
import { formatAbsoluteDate, timeAgo } from "../lib/timeago";
import FleetConnectPanel from "./FleetConnectPanel";

const INPUT =
  "bg-black/30 border border-white/[0.1] rounded px-2 py-1 text-sm text-slate-200 placeholder:text-slate-600";
const BUTTON =
  "px-2.5 py-1 text-xs rounded border border-white/[0.1] text-slate-300 hover:text-slate-100 disabled:opacity-40 disabled:cursor-not-allowed";

type Outcome = { ok: boolean; text: string } | null;

function OutcomeLine({ outcome }: { outcome: Outcome }) {
  if (!outcome) return null;
  return (
    <p role={outcome.ok ? "status" : "alert"} className={`text-xs ${outcome.ok ? "text-emerald-400" : "text-red-400"}`}>
      {outcome.text}
    </p>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="p-4 bg-white/[0.03] rounded-lg border border-white/[0.06] space-y-3">
      <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">{title}</p>
      {children}
    </div>
  );
}

// ── Heartbeat status (all clients) ────────────────────────────────────────────

function HeartbeatStatus({ client }: { client: FleetClient }) {
  return (
    <Section title="Heartbeat">
      <p className="text-sm text-slate-300">
        Last heartbeat:{" "}
        {client.last_heartbeat_at ? (
          <span title={formatAbsoluteDate(client.last_heartbeat_at)}>{timeAgo(client.last_heartbeat_at)}</span>
        ) : (
          "never"
        )}
      </p>
      {client.heartbeat_auth === "shared_key_only" && (
        <p role="note" className="text-xs text-amber-400">
          Using shared key (legacy): this client heartbeats with the fleet-wide key. Rotate its heartbeat key to give
          it its own.
        </p>
      )}
      {!client.has_control_key && (
        <p role="note" className="text-xs text-amber-400">
          No control key -- licence push disabled.
        </p>
      )}
    </Section>
  );
}

// ── Editable settings (registry clients) ──────────────────────────────────────

function InstanceUrlForm({ client }: { client: FleetClient }) {
  const update = useUpdateFleetClient(client.slug);
  const [url, setUrl] = useState(client.mcp_url ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [saving, setSaving] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = url.trim();
    const p = instanceUrlProblem(trimmed);
    setProblem(p);
    setOutcome(null);
    if (p) return;
    setSaving(true);
    try {
      await update.mutateAsync({ mcp_url: trimmed });
      setOutcome({ ok: true, text: "Instance URL saved." });
    } catch (err) {
      setOutcome({ ok: false, text: `Save failed: ${describeMcError(err)}` });
    } finally {
      setSaving(false);
      update.reset();
    }
  }

  return (
    <form onSubmit={save} noValidate className="space-y-1.5">
      <label className="flex flex-col gap-1 text-xs text-slate-400">
        Instance URL
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://<client>.nexus.intodata.nl"
          autoComplete="off"
          className={INPUT}
        />
      </label>
      {problem && <p className="text-[11px] text-red-400">{problem}</p>}
      <div className="flex items-center gap-2">
        <button type="submit" disabled={saving || url.trim() === (client.mcp_url ?? "")} className={BUTTON}>
          {saving ? "Saving..." : "Save URL"}
        </button>
      </div>
      <OutcomeLine outcome={outcome} />
    </form>
  );
}

function ControlKeyForm({ client }: { client: FleetClient }) {
  const update = useUpdateFleetClient(client.slug);
  // The typed key lives here until submitted, then is cleared; the server
  // never returns it, so the UI can only say whether one is configured.
  const [key, setKey] = useState("");
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [saving, setSaving] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = key.trim();
    if (!trimmed) return;
    setOutcome(null);
    setSaving(true);
    try {
      await update.mutateAsync({ control_key: trimmed });
      setKey("");
      setOutcome({ ok: true, text: "Control key saved." });
    } catch (err) {
      setOutcome({ ok: false, text: `Save failed: ${describeMcError(err)}` });
    } finally {
      setSaving(false);
      // The variables held the key: drop the mutation from the cache now.
      update.reset();
    }
  }

  return (
    <form onSubmit={save} noValidate className="space-y-1.5">
      <p className="text-xs text-slate-400">
        Control key:{" "}
        <span
          data-testid="control-key-state"
          className={client.has_control_key ? "text-emerald-400" : "text-amber-400"}
        >
          {client.has_control_key ? "configured" : "not configured"}
        </span>
      </p>
      <label className="flex flex-col gap-1 text-xs text-slate-400">
        {client.has_control_key ? "Replace control key" : "Set control key"}
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          autoComplete="new-password"
          className={INPUT}
        />
        <span className="text-[11px] text-slate-500">
          The client's MCP_OPS_CONTROL_KEY, from /etc/intodata-kb/envs/{client.slug}.env on its server.
        </span>
      </label>
      <button type="submit" disabled={saving || !key.trim()} className={BUTTON}>
        {saving ? "Saving..." : client.has_control_key ? "Replace key" : "Set key"}
      </button>
      <OutcomeLine outcome={outcome} />
    </form>
  );
}

function RotateKey({ client }: { client: FleetClient }) {
  const [confirming, setConfirming] = useState(false);
  const [secret, setSecret] = useState<OneTimeClientKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rotate = useRotateTelemetryKey(client.slug, setSecret);

  async function confirm() {
    setConfirming(false);
    setError(null);
    setSecret(null);
    try {
      await rotate.mutateAsync();
    } catch (err) {
      setError(`Rotate failed: ${describeMcError(err)}`);
    } finally {
      rotate.reset();
    }
  }

  return (
    <div className="space-y-2">
      {!confirming ? (
        <button type="button" onClick={() => setConfirming(true)} disabled={rotate.isPending} className={BUTTON}>
          Rotate heartbeat key
        </button>
      ) : (
        <div role="group" aria-label="Confirm key rotation" className="p-3 rounded border border-amber-500/30 bg-amber-500/5 space-y-2">
          <p className="text-xs text-slate-200">
            Issue a new heartbeat key for {client.slug}? Its current key stops working, so the client stops
            reporting until the new key is on its server and its mcp-server is restarted.
          </p>
          <div className="flex gap-2">
            <button type="button" onClick={() => setConfirming(false)} className={BUTTON}>
              Cancel
            </button>
            <button type="button" onClick={() => void confirm()} className={BUTTON}>
              Rotate
            </button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-red-400">
          {error}
        </p>
      )}
      {secret && <FleetConnectPanel secret={secret} onClose={() => setSecret(null)} />}
    </div>
  );
}

function RemoveClient({ client, onRemoved }: { client: FleetClient; onRemoved: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const remove = useDeleteFleetClient(client.slug);

  return (
    <div className="space-y-2">
      {!confirming ? (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="px-2.5 py-1 text-xs rounded border border-red-500/30 text-red-400 hover:bg-red-500/10"
        >
          Remove client
        </button>
      ) : (
        <div role="group" aria-label="Confirm client removal" className="p-3 rounded border border-red-500/30 bg-red-500/5 space-y-2">
          <p className="text-xs text-slate-200">
            Remove {client.slug} from the registry? Its own heartbeat key and control key are deleted with it, so
            licences can no longer be pushed to it and it can only report with the shared key, if one is still
            configured. Its heartbeat snapshots and issued-licence history are kept.
          </p>
          <div className="flex gap-2">
            <button type="button" onClick={() => setConfirming(false)} className={BUTTON}>
              Cancel
            </button>
            <button
              type="button"
              disabled={remove.isPending}
              onClick={() => remove.mutate(undefined, { onSuccess: onRemoved })}
              className="px-2.5 py-1 text-xs rounded border border-red-500/40 text-red-300 hover:bg-red-500/10"
            >
              {remove.isPending ? "Removing..." : "Remove"}
            </button>
          </div>
        </div>
      )}
      {remove.error && (
        <p role="alert" className="text-xs text-red-400">
          Remove failed: {describeMcError(remove.error)}
        </p>
      )}
    </div>
  );
}

// ── Tab body ──────────────────────────────────────────────────────────────────

export default function FleetClientSettings({
  slug,
  client,
  registryError,
  registryUnavailable = false,
  onRemoved,
}: {
  slug: string;
  client: FleetClient | null;
  registryError?: unknown;
  /** Mission Control could not read its registry file, so it refuses changes: show read-only. */
  registryUnavailable?: boolean;
  onRemoved: () => void;
}) {
  if (!client) {
    return (
      <Section title="Registry">
        {registryUnavailable ? (
          <p role="alert" className="text-sm text-amber-400">
            The Fleet registry file could not be read, so it is unknown whether {slug} is registered. Changes are
            disabled.
          </p>
        ) : registryError ? (
          <p role="alert" className="text-sm text-red-400">
            Client registry unavailable: {describeMcError(registryError)}
          </p>
        ) : (
          <p className="text-sm text-slate-400">
            {slug} is not registered. Add it from the fleet list (Add client) to push licences and manage its keys.
          </p>
        )}
      </Section>
    );
  }

  if (client.source === "file" || registryUnavailable) {
    return (
      <div className="space-y-4">
        <HeartbeatStatus client={client} />
        <Section title="Registry">
          {registryUnavailable ? (
            <p role="alert" className="text-sm text-amber-400">
              The Fleet registry file could not be read; changes are disabled.
            </p>
          ) : (
            <>
              <p role="note" className="text-sm text-slate-300">
                Managed by clients.json
              </p>
              <p className="text-xs text-slate-500">
                This client is defined in Mission Control's clients.json, so it is read-only here. Edit that file to
                change it.
              </p>
            </>
          )}
          <p className="text-xs text-slate-400">Instance URL: {client.mcp_url ?? "-"}</p>
          <p className="text-xs text-slate-400">
            Control key:{" "}
            <span data-testid="control-key-state">{client.has_control_key ? "configured" : "not configured"}</span>
          </p>
        </Section>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <HeartbeatStatus client={client} />
      <Section title="Connection">
        <InstanceUrlForm key={`url-${client.mcp_url ?? ""}`} client={client} />
        <div className="border-t border-white/[0.06] pt-3">
          <ControlKeyForm client={client} />
        </div>
      </Section>
      <Section title="Heartbeat key">
        <RotateKey client={client} />
      </Section>
      <Section title="Remove">
        <RemoveClient client={client} onRemoved={onRemoved} />
      </Section>
    </div>
  );
}
