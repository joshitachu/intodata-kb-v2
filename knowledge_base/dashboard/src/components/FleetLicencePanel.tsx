import { useState } from "react";
import type React from "react";
import {
  describeMcDetail,
  describeMcError,
  type FleetLicence,
  type IssueLicenceRequest,
  type LicenceHistoryEntry,
  type LicencePushResult,
} from "../api/fleet";
import { useClientLicences, useIssueLicence, useLicencePlans } from "../hooks/useFleetLicences";
import { formatDate, generationUsage, licenceStateSentence } from "../lib/licence";
import FleetLicenceBadge from "./FleetLicenceBadge";

const DURATIONS = [1, 3, 12] as const;
type Duration = (typeof DURATIONS)[number];
type StartMode = "now" | "after_current";

const ISSUING_DISABLED_TEXT = "Issuing is not configured on this Mission Control (no signing key)";

function dateOrDash(iso: string | null | undefined): string {
  return formatDate(iso) ?? "-";
}

function months(n: number): string {
  return `${n} month${n === 1 ? "" : "s"}`;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">{label}</span>
      <span className="text-sm text-slate-200">{children}</span>
    </div>
  );
}

// ── Licence card ──────────────────────────────────────────────────────────────

function usageLine(licence: FleetLicence): string {
  const usage = generationUsage(licence);
  if (usage) return `Generations: ${usage.used}/${usage.limit} this period`;
  const used = licence.usage?.generations_used;
  if (typeof used === "number") return `Generations: ${used} this period (no limit reported)`;
  return "Generations: not reported";
}

export function LicenceCard({ licence }: { licence: FleetLicence | null | undefined }) {
  if (!licence) {
    return (
      <div data-testid="licence-card" className="p-4 bg-white/[0.03] rounded-lg border border-white/[0.06] space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">Licence</p>
          <FleetLicenceBadge licence={licence} />
        </div>
        <p className="text-sm text-slate-400">Unknown / instance not reporting licence.</p>
        <p className="text-xs text-slate-500">
          This instance runs a build that does not include licence state in its heartbeat. Update it to see its
          licence here.
        </p>
      </div>
    );
  }

  // `unknown` is the instance's bare fallback ({"state": "unknown"}): it says
  // nothing else, so no other field is interpreted for it. The sentence helper
  // reads only state and period; `enforcement` is filled to satisfy its type.
  const sentence =
    licence.state === "unknown"
      ? "Instance reported an unknown licence state."
      : licenceStateSentence({ ...licence, state: licence.state, enforcement: licence.enforcement ?? "enforce" });

  return (
    <div data-testid="licence-card" className="p-4 bg-white/[0.03] rounded-lg border border-white/[0.06] space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">Licence</p>
        <FleetLicenceBadge licence={licence} />
      </div>
      <p className="text-sm text-slate-300">{sentence}</p>
      {licence.reason && <p className="text-xs text-slate-500">Reason: {licence.reason}</p>}
      <div className="grid grid-cols-2 gap-3">
        <Field label="State">{licence.state}</Field>
        <Field label="Plan">{licence.plan ?? "-"}</Field>
        <Field label="Period">
          {dateOrDash(licence.period_start)} to {dateOrDash(licence.period_end)}
        </Field>
        <Field label="Days remaining">
          {typeof licence.days_remaining === "number" ? licence.days_remaining : "-"}
        </Field>
      </div>
      <p className="text-xs font-mono text-slate-300">{usageLine(licence)}</p>
      {licence.enforcement === "off" && (
        <p role="note" className="text-xs text-amber-400">
          Enforcement is off on this instance: the licence is reported but not enforced.
        </p>
      )}
    </div>
  );
}

// ── Issue form ────────────────────────────────────────────────────────────────

function pushOutcome(
  push: LicencePushResult,
  periodStart: string,
): { ok: boolean | null; text: string } {
  if (!push.attempted) return { ok: null, text: "Not pushed to the instance." };
  if (push.ok) {
    if (push.slot === "next") {
      const starts = dateOrDash(push.next_licence?.period_start ?? periodStart);
      return { ok: true, text: `Queued as next licence (starts ${starts}).` };
    }
    const state =
      push.instance_licence && typeof push.instance_licence === "object" && "state" in push.instance_licence
        ? String((push.instance_licence as { state: unknown }).state)
        : null;
    return { ok: true, text: state ? `Pushed to instance (instance reports: ${state}).` : "Pushed to instance." };
  }
  const status = push.status_code != null ? `HTTP ${push.status_code}` : "no response";
  return { ok: false, text: `Push failed (${status}): ${describeMcDetail(push.detail)}` };
}

function TokenBox({ slug, token }: { slug: string; token: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-1.5">
      <label htmlFor="issued-licence-token" className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">
        Licence for manual install
      </label>
      <textarea
        id="issued-licence-token"
        readOnly
        value={token}
        rows={4}
        className="w-full font-mono text-[11px] bg-black/30 border border-white/[0.1] rounded p-2 text-slate-300 break-all"
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(token).then(() => setCopied(true));
          }}
          className="px-2 py-1 text-xs rounded border border-white/[0.1] text-slate-300 hover:text-slate-100"
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <span className="text-[10px] text-slate-500">
          Bound to client {slug}: it only installs on that instance. It is shown once and not stored here.
        </span>
      </div>
    </div>
  );
}

function IssueForm({
  slug,
  licence,
  currentUsername,
}: {
  slug: string;
  licence: FleetLicence | null | undefined;
  currentUsername: string | null;
}) {
  const plansQuery = useLicencePlans();
  const [planChoice, setPlanChoice] = useState<string>("");
  const [duration, setDuration] = useState<Duration>(1);
  const [startMode, setStartMode] = useState<StartMode>("now");
  const [push, setPush] = useState(true);
  const [returnToken, setReturnToken] = useState(false);
  const [confirming, setConfirming] = useState(false);
  // The signed token lives ONLY here: never in the query cache or storage.
  const [token, setToken] = useState<string | null>(null);
  const issue = useIssueLicence(slug, setToken);

  const plans = plansQuery.data?.plans ?? [];
  const plan = planChoice || plans[0]?.name || "";
  const issuingEnabled = plansQuery.data?.issuing_enabled === true;
  const canRenewAfterCurrent = licence?.state === "valid";
  const effectiveStart: StartMode = canRenewAfterCurrent ? startMode : "now";
  const disabled = !issuingEnabled || issue.isPending || !plansQuery.data;

  const startText =
    effectiveStart === "after_current"
      ? // Mission Control places the start from its issuance ledger, not from this
        // (heartbeat-reported) date; the result line shows the period it chose.
        `after the last issued licence ends (instance reports ${dateOrDash(licence?.period_end)})`
      : "now";

  function submit() {
    if (disabled || !plan) return;
    const body: IssueLicenceRequest = {
      plan,
      months: duration,
      start_after_current: effectiveStart === "after_current",
      push,
      return_token: !push && returnToken,
      issued_by: currentUsername,
    };
    setToken(null);
    setConfirming(false);
    issue.mutate(body);
  }

  return (
    <div className="p-4 bg-white/[0.03] rounded-lg border border-white/[0.06] space-y-3">
      <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">Issue licence</p>

      {plansQuery.isLoading && <p className="text-xs text-slate-500">Loading plans...</p>}
      {plansQuery.error && (
        <p role="alert" className="text-xs text-red-400">
          Plans unavailable: {describeMcError(plansQuery.error)}
        </p>
      )}
      {plansQuery.data && !issuingEnabled && (
        <p role="note" className="text-xs text-amber-400">
          {ISSUING_DISABLED_TEXT}
        </p>
      )}

      <fieldset disabled={disabled} className="space-y-3 disabled:opacity-50">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Plan
          <select
            aria-label="Plan"
            value={plan}
            onChange={(e) => {
              setPlanChoice(e.target.value);
              setConfirming(false);
            }}
            className="bg-black/30 border border-white/[0.1] rounded px-2 py-1 text-sm text-slate-200"
          >
            {plans.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
                {p.description ? ` - ${p.description}` : ""}
              </option>
            ))}
          </select>
        </label>

        <div className="flex flex-col gap-1 text-xs text-slate-400">
          Duration
          <div className="flex gap-3">
            {DURATIONS.map((d) => (
              <label key={d} className="flex items-center gap-1 text-slate-300">
                <input
                  type="radio"
                  name={`licence-duration-${slug}`}
                  checked={duration === d}
                  onChange={() => {
                    setDuration(d);
                    setConfirming(false);
                  }}
                />
                {months(d)}
              </label>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1 text-xs text-slate-400">
          Start
          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-1 text-slate-300">
              <input
                type="radio"
                name={`licence-start-${slug}`}
                checked={effectiveStart === "now"}
                onChange={() => {
                  setStartMode("now");
                  setConfirming(false);
                }}
              />
              Now
            </label>
            <label className="flex items-center gap-1 text-slate-300">
              <input
                type="radio"
                name={`licence-start-${slug}`}
                checked={effectiveStart === "after_current"}
                disabled={!canRenewAfterCurrent}
                onChange={() => {
                  setStartMode("after_current");
                  setConfirming(false);
                }}
              />
              When the current licence ends
              {!canRenewAfterCurrent && <span className="text-slate-500"> (no valid current licence)</span>}
            </label>
          </div>
        </div>

        <label className="flex items-center gap-1.5 text-xs text-slate-300">
          <input
            type="checkbox"
            checked={push}
            onChange={(e) => {
              setPush(e.target.checked);
              if (e.target.checked) setReturnToken(false);
              setConfirming(false);
            }}
          />
          Push to instance
        </label>
        {!push && (
          <label className="flex items-center gap-1.5 text-xs text-slate-300 pl-4">
            <input
              type="checkbox"
              checked={returnToken}
              onChange={(e) => {
                setReturnToken(e.target.checked);
                setConfirming(false);
              }}
            />
            Show licence for manual install
          </label>
        )}

        {!confirming ? (
          <button
            type="button"
            disabled={disabled || !plan}
            onClick={() => {
              if (!disabled && plan) setConfirming(true);
            }}
            className="px-3 py-1.5 text-xs rounded border border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {push ? "Issue and push" : "Issue"}
          </button>
        ) : (
          <div
            role="group"
            aria-label="Confirm licence issue"
            className="p-3 rounded border border-amber-500/30 bg-amber-500/5 space-y-2"
          >
            <p data-testid="issue-confirm-summary" className="text-xs text-slate-200">
              Issue a {plan} licence for {slug}: {months(duration)}, starting {startText}.{" "}
              {push ? "It will be pushed to the instance." : "It will not be pushed."}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="px-2 py-1 text-xs rounded border border-white/[0.1] text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={submit}
                className="px-2 py-1 text-xs rounded border border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10"
              >
                Confirm
              </button>
            </div>
          </div>
        )}
      </fieldset>

      {issue.isPending && <p className="text-xs text-slate-500">Issuing...</p>}
      {issue.error && (
        <p role="alert" className="text-xs text-red-400">
          Issue failed: {describeMcError(issue.error)}
        </p>
      )}
      {issue.data && (
        <div data-testid="issue-result" className="space-y-1.5 text-xs">
          <p className="text-emerald-400">
            Licence {issue.data.licence_id} issued ({issue.data.plan}): {dateOrDash(issue.data.period_start)} to{" "}
            {dateOrDash(issue.data.period_end)}.
          </p>
          {(() => {
            const outcome = pushOutcome(issue.data.push, issue.data.period_start);
            const color = outcome.ok === false ? "text-red-400" : outcome.ok ? "text-emerald-400" : "text-slate-400";
            return (
              <p role={outcome.ok === false ? "alert" : undefined} className={color}>
                {outcome.text}
              </p>
            );
          })()}
          {token && <TokenBox slug={slug} token={token} />}
        </div>
      )}
    </div>
  );
}

// ── History ───────────────────────────────────────────────────────────────────

function pushCell(entry: LicenceHistoryEntry): string {
  if (entry.push_ok == null) return "not pushed";
  if (entry.push_ok) return "ok";
  return entry.push_status_code != null ? `failed (${entry.push_status_code})` : "failed";
}

function LicenceHistory({ slug }: { slug: string }) {
  const history = useClientLicences(slug);
  return (
    <div className="space-y-2">
      <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">Issued licences</p>
      {history.isLoading && <p className="text-xs text-slate-500">Loading history...</p>}
      {history.error && (
        <p role="alert" className="text-xs text-red-400">
          History unavailable: {describeMcError(history.error)}
        </p>
      )}
      {history.data && history.data.length === 0 && (
        <p className="text-xs text-slate-500">No licences issued for this client yet.</p>
      )}
      {history.data && history.data.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-white/[0.06]">
          <table className="w-full text-xs" aria-label="Licence history">
            <thead>
              <tr className="border-b border-white/[0.06] bg-white/[0.03]">
                <th className="text-left px-3 py-2 text-slate-500 font-mono font-normal">Issued</th>
                <th className="text-left px-3 py-2 text-slate-500 font-mono font-normal">Plan</th>
                <th className="text-left px-3 py-2 text-slate-500 font-mono font-normal">Period</th>
                <th className="text-left px-3 py-2 text-slate-500 font-mono font-normal">By</th>
                <th className="text-left px-3 py-2 text-slate-500 font-mono font-normal">Push</th>
              </tr>
            </thead>
            <tbody>
              {history.data.map((entry) => (
                <tr key={entry.licence_id} className="border-b border-white/[0.04] last:border-0">
                  <td className="px-3 py-2 text-slate-400">{dateOrDash(entry.issued_at)}</td>
                  <td className="px-3 py-2 text-slate-300">{entry.plan}</td>
                  <td className="px-3 py-2 text-slate-400">
                    {dateOrDash(entry.period_start)} to {dateOrDash(entry.period_end)}
                  </td>
                  <td className="px-3 py-2 text-slate-400">{entry.issued_by ?? "-"}</td>
                  <td className={`px-3 py-2 ${entry.push_ok === false ? "text-red-400" : "text-slate-400"}`}>
                    {pushCell(entry)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Tab body ──────────────────────────────────────────────────────────────────

export default function FleetLicencePanel({
  slug,
  licence,
  currentUsername,
}: {
  slug: string;
  licence: FleetLicence | null | undefined;
  currentUsername: string | null;
}) {
  return (
    <div className="space-y-4">
      <LicenceCard licence={licence} />
      <IssueForm slug={slug} licence={licence} currentUsername={currentUsername} />
      <LicenceHistory slug={slug} />
    </div>
  );
}
