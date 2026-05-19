import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useGenerateRuns, useReviews } from "../hooks/useProjects";
import { api } from "../api/client";
import type { RunComparisonRun } from "../types";

interface Props {
  projectId: string;
  preloadRunA?: number | null;
  preloadRunB?: number | null;
}

function formatValue(value: unknown, suffix = "") {
  if (value == null) return "-";
  if (typeof value === "number") {
    return `${Number.isInteger(value) ? value : value.toFixed(3)}${suffix}`;
  }
  return String(value);
}

function formatDelta(value: number | null | undefined, suffix = "") {
  if (value == null) return "-";
  const formatted = `${Number.isInteger(value) ? value : value.toFixed(3)}${suffix}`;
  return value > 0 ? `+${formatted}` : formatted;
}

function qualityBreakdown(run: RunComparisonRun) {
  const details = run.doc_quality ?? {};
  return Object.entries(details)
    .filter(([key, value]) => key !== "overall" && typeof value === "number")
    .map(([key, value]) => ({ key, value: value as number }));
}

export default function ABComparison({ projectId, preloadRunA, preloadRunB }: Props) {
  const { data: runsData } = useGenerateRuns(projectId);
  const runs = (runsData?.runs ?? []).filter((r) => r.status === "completed");

  const [runA, setRunA] = useState<number | null>(preloadRunA ?? null);
  const [runB, setRunB] = useState<number | null>(preloadRunB ?? null);

  useEffect(() => {
    if (preloadRunA != null) setRunA(preloadRunA);
    if (preloadRunB != null) setRunB(preloadRunB);
  }, [preloadRunA, preloadRunB]);

  const reviewA = useReviews(projectId, runA);
  const reviewB = useReviews(projectId, runB);

  const { data: comparison, isLoading } = useQuery({
    queryKey: ["run-comparison", projectId, runA, runB],
    queryFn: () => api.compareGenerateRuns(projectId, runA!, runB!),
    enabled: runA != null && runB != null && runA !== runB,
  });

  if (runs.length < 2) {
    return (
      <div className="card-dark p-6 text-sm text-slate-500 font-mono">
        Need at least 2 completed runs to compare.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Run selectors */}
      <div className="flex items-center gap-4">
        <div className="flex-1">
          <label className="text-xs text-slate-500 mb-1 block">Run A</label>
          <select
            value={runA ?? ""}
            onChange={(e) => setRunA(e.target.value ? Number(e.target.value) : null)}
            className="input-dark w-full"
          >
            <option value="">Select run...</option>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                #{r.id} - {new Date(r.started_at).toLocaleDateString()} ({r.doc_count} docs)
              </option>
            ))}
          </select>
        </div>
        <span className="text-slate-600 text-lg mt-5">vs</span>
        <div className="flex-1">
          <label className="text-xs text-slate-500 mb-1 block">Run B</label>
          <select
            value={runB ?? ""}
            onChange={(e) => setRunB(e.target.value ? Number(e.target.value) : null)}
            className="input-dark w-full"
          >
            <option value="">Select run...</option>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                #{r.id} - {new Date(r.started_at).toLocaleDateString()} ({r.doc_count} docs)
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Comparison results */}
      {isLoading && (
        <div className="p-4 text-sm text-slate-500">Loading comparison...</div>
      )}

      {comparison && (
        <div className="card-dark">
          <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-mono text-slate-400">
                Run #{comparison.run_a.id} vs #{comparison.run_b.id}
              </h3>
              <span className={`px-2 py-1 rounded text-xs font-mono ${
                comparison.verdict === "PASS"
                  ? "bg-[#a0e06f]/10 text-[#a0e06f]"
                  : "bg-red-500/10 text-red-400"
              }`}>
                {comparison.verdict}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 p-4 border-b border-white/[0.05]">
            {[comparison.run_a, comparison.run_b].map((run) => (
              <div key={run.id} className="bg-white/[0.02] rounded p-3 border border-white/[0.06]">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-mono text-xs text-slate-500">Run #{run.id}</span>
                  <span className={`text-xs font-mono ${
                    run.quality_pass ? "text-[#a0e06f]" : "text-amber-400"
                  }`}>
                    {run.quality == null ? "NO SCORE" : run.quality_pass ? "QUALITY PASS" : "QUALITY FAIL"}
                  </span>
                </div>
                <div className="space-y-1 text-xs">
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-600">Analysis</span>
                    <span className="font-mono text-slate-400 truncate">{run.analysis_model ?? "-"}</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-600">Writing</span>
                    <span className="font-mono text-slate-400 truncate">{run.write_model ?? "-"}</span>
                  </div>
                  {!run.metrics_found && (
                    <div className="text-amber-400 pt-1">Metrics file missing; showing database fields only.</div>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Metrics comparison */}
          <div className="p-4">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.04] text-left font-mono text-xs text-slate-600 uppercase">
                  <th className="px-4 py-2 font-medium">Metric</th>
                  <th className="px-4 py-2 font-medium">Run A (#{comparison.run_a.id})</th>
                  <th className="px-4 py-2 font-medium">Run B (#{comparison.run_b.id})</th>
                  <th className="px-4 py-2 font-medium">Diff</th>
                </tr>
              </thead>
              <tbody>
                {([
                  { label: "Doc Count", key: "doc_count" },
                  { label: "Quality", key: "quality" },
                  { label: "Duration (ms)", key: "duration_ms" },
                  { label: "Input Tokens", key: "input_tokens" },
                  { label: "Output Tokens", key: "output_tokens" },
                  { label: "Cost (USD)", key: "cost_usd" },
                ] as Array<{ label: string; key: keyof RunComparisonRun }>).map(({ label, key }) => {
                  const va = comparison.run_a[key];
                  const vb = comparison.run_b[key];
                  const diff = comparison.delta[String(key)];
                  const pct = comparison.delta_pct[String(key)];
                  return (
                    <tr key={key} className="border-b border-white/[0.04]">
                      <td className="px-4 py-2 text-slate-400">{label}</td>
                      <td className="px-4 py-2 font-mono text-slate-400">{formatValue(va)}</td>
                      <td className="px-4 py-2 font-mono text-slate-400">{formatValue(vb)}</td>
                      <td className={`px-4 py-2 font-mono ${
                        diff == null ? "text-slate-600" :
                        diff > 0 ? "text-amber-400" :
                        diff < 0 ? "text-[#a0e06f]" :
                        "text-slate-600"
                      }`}>
                        {formatDelta(diff)}
                        {pct != null && <span className="ml-1 text-slate-600">({formatDelta(pct, "%")})</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="px-4 pb-4">
            <h4 className="text-xs font-mono text-slate-500 uppercase mb-2">
              Quality Detail Breakdown
            </h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {[comparison.run_a, comparison.run_b].map((run) => {
                const breakdown = qualityBreakdown(run);
                return (
                  <div key={run.id} className="bg-white/[0.02] rounded p-3 border border-white/[0.06]">
                    <div className="font-mono text-xs text-slate-500 mb-2">Run #{run.id}</div>
                    {breakdown.length === 0 ? (
                      <p className="text-xs text-slate-600">No per-category scores available.</p>
                    ) : (
                      <div className="space-y-1">
                        {breakdown.map(({ key, value }) => (
                          <div key={key} className="flex justify-between text-xs">
                            <span className="text-slate-500">{key.replace(/_/g, " ")}</span>
                            <span className="font-mono text-slate-400">{formatValue(value)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Human review side-by-side */}
          <div className="px-4 pb-4 border-t border-white/[0.05] pt-4">
            <h4 className="text-xs font-mono text-slate-500 uppercase mb-2">Human Review</h4>
            {reviewA.isLoading || reviewB.isLoading ? (
              <p className="text-xs text-slate-500">Loading reviews...</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {([
                  { run: comparison.run_a, data: reviewA.data },
                  { run: comparison.run_b, data: reviewB.data },
                ] as const).map(({ run, data }) => (
                  <div key={run.id} className="bg-white/[0.02] rounded p-3 border border-white/[0.06]">
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-mono text-xs text-slate-500">Run #{run.id}</span>
                      {data && data.total_reviews > 0 ? (
                        <span className={`text-xs font-mono ${data.pass_result ? "text-[#a0e06f]" : "text-red-400"}`}>
                          {data.pass_result ? "PASS" : "FAIL"} &mdash; {data.total_reviews} review{data.total_reviews !== 1 ? "s" : ""}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-600">No reviews</span>
                      )}
                    </div>
                    {!data || data.total_reviews === 0 ? (
                      <p className="text-xs text-slate-600">No reviews submitted yet.</p>
                    ) : (
                      <div className="space-y-1">
                        <div className="flex justify-between text-xs border-b border-white/[0.04] pb-1 mb-1">
                          <span className="text-slate-500 font-mono">Overall</span>
                          <span className="font-mono text-slate-300">{data.average_overall.toFixed(2)}</span>
                        </div>
                        {Object.entries(data.average_per_question).map(([q, score]) => (
                          <div key={q} className="flex justify-between text-xs">
                            <span className="text-slate-600">{q.replace(/_/g, " ")}</span>
                            <span className="font-mono text-slate-400">{(score as number).toFixed(2)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
