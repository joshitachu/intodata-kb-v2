import { useGenerateRuns } from "../hooks/useProjects";
import type { GenerateRun } from "../types";

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

  if (!projectId) return null;

  const runs = data?.runs ?? [];

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
                <tr key={run.id} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]">
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
                        <span className="text-slate-400" title="Analysis model">{run.model_name.split("/")[1] ?? run.model_name}</span>
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
