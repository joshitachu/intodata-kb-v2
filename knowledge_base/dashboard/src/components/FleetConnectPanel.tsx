import { useState } from "react";
import type { OneTimeClientKey } from "../hooks/useFleetClients";
import { MISSING_TELEMETRY_URL_TEXT, clientEnvLines, deployClientCommands } from "../lib/fleetClient";

function CopyBlock({ id, label, value, rows }: { id: string; label: string; value: string; rows: number }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">
        {label}
      </label>
      <textarea
        id={id}
        readOnly
        value={value}
        rows={rows}
        spellCheck={false}
        autoComplete="off"
        className="w-full font-mono text-[11px] bg-black/30 border border-white/[0.1] rounded p-2 text-slate-300 break-all"
      />
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard?.writeText(value).then(() => setCopied(true));
        }}
        className="px-2 py-1 text-xs rounded border border-white/[0.1] text-slate-300 hover:text-slate-100"
      >
        {copied ? "Copied" : `Copy ${label.toLowerCase()}`}
      </button>
    </div>
  );
}

/**
 * The one-time "Connect this client" block shown after a client is added or
 * its heartbeat key is rotated. The key is a prop from the owner's component
 * state: it is never fetched again, cached or stored, so closing this panel
 * (or unmounting its owner) is the last time it is visible.
 */
export default function FleetConnectPanel({ secret, onClose }: { secret: OneTimeClientKey; onClose: () => void }) {
  const env = secret.clientEnv;
  return (
    <section
      aria-label="Connect this client"
      data-testid="connect-panel"
      className="p-4 rounded-lg border border-emerald-500/30 bg-emerald-500/5 space-y-3"
    >
      <p className="text-sm font-semibold text-slate-100">Connect this client ({secret.slug})</p>
      <p role="note" className="text-xs text-amber-300">
        This key is shown once. Paste it on the client's server, then redeploy/restart its mcp-server.
      </p>
      {env.MCP_OPS_TELEMETRY_URL == null && (
        <p role="alert" className="text-xs text-amber-400">
          MCP_OPS_TELEMETRY_URL: {MISSING_TELEMETRY_URL_TEXT}.
        </p>
      )}
      <CopyBlock
        id={`connect-deploy-${secret.slug}`}
        label="Deploy commands"
        value={deployClientCommands(env)}
        rows={4}
      />
      <p className="text-[11px] text-slate-500">
        The first command writes the key to a root-only file so it never appears on a command line; run it with a
        leading space to keep it out of shell history. Alternatively, add these env lines to the client's env file:
      </p>
      <CopyBlock id={`connect-env-${secret.slug}`} label="Env lines" value={clientEnvLines(env)} rows={3} />
      <div className="flex justify-end">
        <button
          type="button"
          onClick={onClose}
          className="px-3 py-1.5 text-xs rounded border border-white/[0.1] text-slate-300 hover:text-slate-100"
        >
          I have copied it, close
        </button>
      </div>
    </section>
  );
}
