import { useState } from "react";
import { useGenerateRuns } from "../hooks/useProjects";
import { useRunConnectorEvidence } from "../hooks/useConnectors";
import type { GenerateRun, RunRuntimeEvidenceSummary } from "../types";

interface Props {
  projectId: string | null;
  onViewLog: (run: GenerateRun) => void;
  onCompare?: (runAId: number, runBId: number) => void;
}

const statusColors: Record<string, string> = {
  running: "border-accent-primary/40 text-accent-primary",
  completed: "border-[#a0e06f]/40 text-[#a0e06f]",
  failed: "border-red-400/40 text-red-400",
};

function formatDuration(ms: number | null): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem}s`;
}

function formatTokens(n: number | null): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatCost(usd: number | null): string {
  if (usd == null) return "-";
  return `$${usd.toFixed(4)}`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function RunHistory({ projectId, onViewLog, onCompare }: Props) {
  const { data, isLoading } = useGenerateRuns(projectId);

  const runs = data?.runs ?? [];

  if (!projectId) return null;

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
        <h3 className="text-sm font-mono text-slate-400">
          Generation History
          {runs.length > 0 && (
            <span className="ml-2 text-xs text-slate-600">
              ({runs.length} run{runs.length !== 1 ? "s" : ""})
            </span>
          )}
        </h3>
      </div>

      {isLoading ? (
        <div className="p-6 text-sm text-slate-500">Loading...</div>
      ) : runs.length === 0 ? (
        <div className="p-6 text-sm text-slate-500 font-mono">
          No generation runs yet for this project.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.04] text-left font-mono text-xs text-slate-600 uppercase tracking-wider">
                <th className="px-4 py-2.5 font-medium">Date</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Verdict</th>
                <th className="px-4 py-2.5 font-medium">Model</th>
                <th className="px-4 py-2.5 font-medium">Duration</th>
                <th className="px-4 py-2.5 font-medium">Tokens In</th>
                <th className="px-4 py-2.5 font-medium">Tokens Out</th>
                <th className="px-4 py-2.5 font-medium">Cost</th>
                <th className="px-4 py-2.5 font-medium">Quality</th>
                <th className="px-4 py-2.5 font-medium">Docs</th>
                <th className="px-4 py-2.5 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <LegacyRunRow
                  key={run.id}
                  run={run}
                  onViewLog={onViewLog}
                  onCompare={onCompare}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LegacyRunRow({
  run,
  onViewLog,
  onCompare,
}: {
  run: GenerateRun;
  onViewLog: (run: GenerateRun) => void;
  onCompare?: (runAId: number, runBId: number) => void;
}) {
  return (
    <tr className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]">
      <td className="px-4 py-2.5 whitespace-nowrap text-slate-400">
        {formatDate(run.started_at)}
      </td>
      <td className="px-4 py-2.5">
        <span
          className={`status-chip ${
            statusColors[run.status] ?? "border-slate-500/40 text-slate-500"
          }`}
        >
          {run.status.toUpperCase()}
        </span>
      </td>
      <td className="px-4 py-2.5">
        {run.run_verdict ? (
          <span
            className={`status-chip text-xs ${
              run.run_verdict === "client_ready"
                ? "border-[#a0e06f]/40 text-[#a0e06f]"
                : run.run_verdict === "published" || run.run_verdict === "draft"
                  ? "border-amber-400/40 text-amber-400"
                  : "border-red-400/40 text-red-400"
            }`}
            title={
              run.gate_results
                ? "Enterprise gate verdict available in run detail"
                : `Run verdict: ${run.run_verdict}`
            }
          >
            {run.run_verdict.replace(/_/g, " ").toUpperCase()}
          </span>
        ) : run.auto_verdict ? (
          <button
            className={`status-chip text-xs ${
              run.auto_verdict === "PASS"
                ? "border-[#a0e06f]/40 text-[#a0e06f]"
                : "border-red-400/40 text-red-400"
            }`}
            title={
              run.auto_baseline_run_id
                ? `vs run #${run.auto_baseline_run_id} — click to compare`
                : `Auto verdict: ${run.auto_verdict}`
            }
            onClick={() =>
              run.auto_baseline_run_id && onCompare?.(run.auto_baseline_run_id, run.id)
            }
            disabled={!run.auto_baseline_run_id || !onCompare}
          >
            {run.auto_verdict}
          </button>
        ) : (
          <span className="text-slate-600 text-xs">—</span>
        )}
      </td>
      <td className="px-4 py-2.5 whitespace-nowrap text-xs font-mono">
        {run.model_name ? (
          <span className="flex flex-col gap-0.5">
            <span className="text-slate-400" title="Analysis model">
              {run.model_name.split("/")[1] ?? run.model_name}
            </span>
            {run.write_model_name && (
              <span className="text-accent-primary/60" title="Write model (Phase 3)">
                ✎ {run.write_model_name.split("/")[1] ?? run.write_model_name}
              </span>
            )}
          </span>
        ) : (
          <span className="text-slate-600">—</span>
        )}
      </td>
      <td className="px-4 py-2.5 whitespace-nowrap text-slate-400 font-mono">
        {formatDuration(run.duration_ms)}
      </td>
      <td className="px-4 py-2.5 whitespace-nowrap text-slate-400 font-mono">
        {formatTokens(run.input_tokens)}
      </td>
      <td className="px-4 py-2.5 whitespace-nowrap text-slate-400 font-mono">
        {formatTokens(run.output_tokens)}
      </td>
      <td className="px-4 py-2.5 whitespace-nowrap font-mono text-slate-400">
        {formatCost(run.cost_usd)}
      </td>
      <td className="px-4 py-2.5 whitespace-nowrap font-mono">
        {run.quality_score != null ? (
          <span
            className={
              run.quality_score >= 4.0
                ? "text-[#a0e06f]"
                : run.quality_score >= 3.0
                  ? "text-amber-400"
                  : "text-red-400"
            }
            title={`Quality score: ${run.quality_score.toFixed(2)}/5.0`}
          >
            {run.quality_score.toFixed(1)}
          </span>
        ) : (
          <span className="text-slate-600">—</span>
        )}
      </td>
      <td className="px-4 py-2.5 text-slate-400 font-mono">{run.doc_count}</td>
      <td className="px-4 py-2.5">
        {run.log_file && (
          <button
            className="text-xs font-medium text-accent-primary/70 hover:text-accent-primary transition-colors"
            onClick={() => onViewLog(run)}
          >
            View Log
          </button>
        )}
        <RunEvidenceButton runId={run.id} />
      </td>
    </tr>
  );
}

/** "tmc" -> "Runtime (TMC)". No source key -> plain "Runtime". */
function runtimeLabel(sourceKey: string | null): string {
  return sourceKey ? `Runtime (${sourceKey.toUpperCase()})` : "Runtime";
}

/**
 * Does the runtime source list jobs this scan does not contain?
 *
 * `scope_mismatch` is computed server-side against an orphan-ratio threshold,
 * so it is READ, not re-derived: a low match count on its own is normal and
 * correct (the runtime source lists scheduled tasks, and most scanned units are
 * child jobs that are never scheduled), and treating it as a fault produces a
 * confident false alarm on a healthy project.
 *
 * The fallback exists only for coverage artifacts written before the field --
 * `scope_mismatch === null`. A payload that reports `false` denies the signal
 * and the guess stays silent.
 */
function runtimeScopeMismatch(runtime: RunRuntimeEvidenceSummary): boolean {
  if (!runtime.available) return false;
  if (runtime.scope_mismatch != null) return runtime.scope_mismatch;
  return runtime.mode === "binding_suspect" ||
    (runtime.units_matched === 0 && runtime.orphan_runtime_job_count > 0);
}

/** "5/120 units matched", or the absence of an artifact for that sub-project. */
function subprojectLine(row: RunRuntimeEvidenceSummary): string {
  const name = row.subproject ?? "unnamed sub-project";
  if (!row.available) return `${name}: no coverage artifact`;
  return `${name}: ${row.units_matched}/${row.units_requested} units matched`;
}

/**
 * Coverage of the mirrored runtime lane for a run.
 *
 * Absence is stated, never silent: the block always renders exactly one of a
 * coverage line, a scope observation, or the skipped/unavailable line. A run
 * that shipped no runtime data must not read as a run whose jobs never execute.
 *
 * Counts only, by design -- unit and runtime job NAMES stay server-side.
 */
function RuntimeEvidenceBlock({
  runtime,
  subprojects,
}: {
  runtime: RunRuntimeEvidenceSummary;
  subprojects: RunRuntimeEvidenceSummary[];
}) {
  const label = runtimeLabel(runtime.source_key);
  const window = runtime.window_days ? ` - ${runtime.window_days}-day window` : "";
  const scopeMismatch = runtimeScopeMismatch(runtime);
  const hasCoverage = runtime.available && !scopeMismatch && runtime.units_matched > 0;
  const orphans = runtime.orphan_runtime_job_count;
  // An aggregate spans several sub-projects; a composite CHILD run names the one
  // sub-project it describes. The two are mutually exclusive server-side.
  const isAggregate = runtime.available && runtime.subproject_count > 1;

  return (
    <div className="mt-2 border-t border-white/[0.06] pt-2">
      {scopeMismatch ? (
        <p className="text-amber-400">
          {label}: {runtime.units_matched}/{runtime.units_requested} units matched. {orphans}{" "}
          runtime job{orphans === 1 ? " has" : "s have"} no scanned counterpart - the runtime
          source lists jobs this scan does not contain.
        </p>
      ) : hasCoverage ? (
        <p className="text-slate-300">
          {label}: {runtime.units_matched}/{runtime.units_requested} units matched{window}
        </p>
      ) : (
        <p className="text-slate-500">Runtime evidence was skipped or unavailable for this run.</p>
      )}
      {hasCoverage && runtime.units_matched < runtime.units_requested && (
        // Said plainly so a low ratio is not read as a defect: the runtime source
        // is the deployed orchestration surface, not the artifact inventory.
        <p className="mt-1 text-slate-600">
          Unmatched units are expected: the runtime source lists scheduled tasks, not every
          scanned unit.
        </p>
      )}
      {runtime.available && runtime.subproject && (
        <p className="mt-1 text-slate-500">Describes sub-project {runtime.subproject}.</p>
      )}
      {isAggregate && (
        <p className="mt-1 text-slate-500">
          Aggregate across {runtime.subproject_count} sub-projects.
        </p>
      )}
      {isAggregate && subprojects.length > 0 && (
        <ul className="mt-1 space-y-0.5 font-mono text-slate-500">
          {subprojects.map((row, index) => (
            <li key={row.subproject ?? `row-${index}`}>{subprojectLine(row)}</li>
          ))}
        </ul>
      )}
      {runtime.available && runtime.partial && (
        <p className="mt-1 text-amber-400">Coverage is partial: not every batch was retrieved.</p>
      )}
      {runtime.available && (
        // The coverage artifact is read from the project's published docs. Per-run
        // archival does not exist, so this figure can describe a later run.
        <p className="mt-1 text-slate-600">
          Describes the latest published docs for this project, not this run.
        </p>
      )}
    </div>
  );
}

function RunEvidenceButton({ runId }: { runId: number }) {
  const [open, setOpen] = useState(false);
  const evidence = useRunConnectorEvidence(runId, open);
  return (
    <div className="mt-1.5">
      <button
        className="text-xs font-medium text-accent-primary/70 hover:text-accent-primary transition-colors"
        onClick={() => setOpen((current) => !current)}
      >
        {open ? "Hide Evidence" : "Evidence"}
      </button>
      {open && (
        <div className="mt-2 min-w-64 rounded border border-white/[0.08] bg-surface-raised p-3 text-xs">
          {evidence.isLoading && <p className="text-slate-500">Loading evidence summary…</p>}
          {evidence.isError && <p className="text-slate-500">No connector evidence summary is available for this run.</p>}
          {evidence.data?.available && <>
            <p className="text-slate-300">{evidence.data.successful_calls} successful · {evidence.data.failed_calls} failed · {evidence.data.denied_calls} denied{evidence.data.truncated_calls ? ` · ${evidence.data.truncated_calls} truncated` : ""}</p>
            {(evidence.data.observation_from || evidence.data.observation_to) && <p className="mt-1 text-slate-500">Observed {evidence.data.observation_from ? new Date(evidence.data.observation_from).toLocaleString() : "?"} – {evidence.data.observation_to ? new Date(evidence.data.observation_to).toLocaleString() : "?"}</p>}
            {evidence.data.findings.length > 0 && <ul className="mt-2 space-y-1 text-slate-400">{evidence.data.findings.slice(0, 3).map((finding) => <li key={finding.evidence_id}><span className="font-mono text-slate-500">{finding.classification}</span>{finding.summary ? ` — ${finding.summary}` : ""}</li>)}</ul>}
            {evidence.data.limitations.length > 0 && <p className="mt-2 text-amber-400">{evidence.data.limitations.join(", ")}</p>}
          </>}
          {evidence.data && (
            <RuntimeEvidenceBlock
              runtime={evidence.data.runtime}
              subprojects={evidence.data.runtime_subprojects ?? []}
            />
          )}
        </div>
      )}
    </div>
  );
}
