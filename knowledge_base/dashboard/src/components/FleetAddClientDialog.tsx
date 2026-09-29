import { useState } from "react";
import type React from "react";
import { describeMcError } from "../api/fleet";
import { useAddFleetClient, type OneTimeClientKey } from "../hooks/useFleetClients";
import { instanceUrlProblem, slugProblem } from "../lib/fleetClient";
import FleetConnectPanel from "./FleetConnectPanel";

/**
 * What the dialog may be opened with. There is deliberately no instance-URL
 * prefill: the only other source is a heartbeat, which is client-controlled
 * (ADR-038/039), and the registered URL is where Mission Control sends the
 * control key and licences. The operator types it.
 */
export interface AddClientPrefill {
  slug?: string;
  /** The URL a heartbeat reported; shown as unverified plain text, never used as a value or link. */
  reportedUrl?: string | null;
}

const INPUT =
  "bg-black/30 border border-white/[0.1] rounded px-2 py-1 text-sm text-slate-200 placeholder:text-slate-600";

function FieldError({ id, text }: { id: string; text: string | null }) {
  if (!text) return null;
  return (
    <span id={id} className="text-[11px] text-red-400">
      {text}
    </span>
  );
}

/**
 * Register a client with Mission Control. On success the dialog turns into the
 * one-time connect panel; the heartbeat key lives only in this component's
 * state and is gone when the dialog closes.
 */
export default function FleetAddClientDialog({
  prefill,
  onClose,
}: {
  prefill?: AddClientPrefill;
  onClose: () => void;
}) {
  const [slug, setSlug] = useState(prefill?.slug ?? "");
  const [mcpUrl, setMcpUrl] = useState("");
  const [controlKey, setControlKey] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [secret, setSecret] = useState<OneTimeClientKey | null>(null);
  const add = useAddFleetClient(setSecret);

  const trimmedSlug = slug.trim();
  const trimmedUrl = mcpUrl.trim();
  const slugError = submitted ? slugProblem(trimmedSlug) : null;
  const urlError = submitted ? instanceUrlProblem(trimmedUrl) : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    if (slugProblem(trimmedSlug) || instanceUrlProblem(trimmedUrl)) return;
    setServerError(null);
    setSubmitting(true);
    try {
      await add.mutateAsync({ slug: trimmedSlug, mcp_url: trimmedUrl, control_key: controlKey.trim() || null });
      setControlKey("");
    } catch (err) {
      setServerError(`Add failed: ${describeMcError(err)}`);
    } finally {
      setSubmitting(false);
      // The request carried the control key: drop the mutation (and its
      // variables) from the cache now rather than when the dialog unmounts.
      add.reset();
    }
  }

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40" onClick={secret ? undefined : onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Add client"
        className="fixed top-16 left-1/2 -translate-x-1/2 w-full max-w-lg bg-[#0f1117] border border-white/[0.08] rounded-xl z-50 p-5 space-y-4 shadow-2xl"
      >
        <div className="flex items-start justify-between">
          <h2 className="text-base font-semibold text-slate-100">{secret ? "Client added" : "Add client"}</h2>
          {!secret && (
            <button
              type="button"
              aria-label="Close"
              onClick={onClose}
              className="text-slate-500 hover:text-slate-300 text-lg leading-none"
            >
              ×
            </button>
          )}
        </div>

        {secret ? (
          <FleetConnectPanel secret={secret} onClose={onClose} />
        ) : (
          <form onSubmit={submit} noValidate className="space-y-3">
            <label className="flex flex-col gap-1 text-xs text-slate-400">
              Slug
              <input
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="acme"
                autoComplete="off"
                aria-invalid={slugError ? true : undefined}
                aria-describedby={slugError ? "add-client-slug-error" : undefined}
                className={INPUT}
              />
              <FieldError id="add-client-slug-error" text={slugError} />
            </label>

            <label className="flex flex-col gap-1 text-xs text-slate-400">
              Instance URL
              <input
                value={mcpUrl}
                onChange={(e) => setMcpUrl(e.target.value)}
                placeholder="https://<client>.nexus.intodata.nl"
                autoComplete="off"
                aria-invalid={urlError ? true : undefined}
                aria-describedby={urlError ? "add-client-url-error" : undefined}
                className={INPUT}
              />
              <FieldError id="add-client-url-error" text={urlError} />
            </label>
            {prefill?.reportedUrl && (
              <p data-testid="reported-url-hint" className="-mt-1 text-[11px] text-slate-500 break-all">
                Reported by the instance (unverified) -- type the address yourself:{" "}
                <span className="font-mono text-slate-400">{prefill.reportedUrl}</span>
              </p>
            )}

            <label className="flex flex-col gap-1 text-xs text-slate-400">
              Control key (optional)
              <input
                type="password"
                value={controlKey}
                onChange={(e) => setControlKey(e.target.value)}
                autoComplete="new-password"
                aria-describedby="add-client-control-key-help"
                className={INPUT}
              />
              <span id="add-client-control-key-help" className="text-[11px] text-slate-500">
                The client's MCP_OPS_CONTROL_KEY, from /etc/intodata-kb/envs/&lt;client&gt;.env on its server; you
                can add it later.
              </span>
            </label>

            {serverError && (
              <p role="alert" className="text-xs text-red-400">
                {serverError}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-1.5 text-xs rounded border border-white/[0.1] text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="px-3 py-1.5 text-xs rounded border border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10 disabled:opacity-40"
              >
                {submitting ? "Adding..." : "Add client"}
              </button>
            </div>
          </form>
        )}
      </div>
    </>
  );
}
