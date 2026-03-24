import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useGenerateRuns } from "../../hooks/useProjects";

const BASE = "";

async function fetchComparison(projectId: string, a: number, b: number) {
  const res = await fetch(
    `${BASE}/mcp/generate-runs/${projectId}/compare?a=${a}&b=${b}`,
    { credentials: "include" }
  );
  if (!res.ok) throw new Error(`Compare failed: ${res.status}`);
  return res.json();
}

interface Props {
  projectId: string;
}

export default function ABComparison({ projectId }: Props) {
  const { data: runsData } = useGenerateRuns(projectId);
  const runs = (runsData?.runs ?? []).filter((r) => r.status === "completed");

  const [runA, setRunA] = useState<number | null>(null);
  const [runB, setRunB] = useState<number | null>(null);

  const { data: comparison, isLoading } = useQuery({
    queryKey: ["run-comparison", projectId, runA, runB],
    queryFn: () => fetchComparison(projectId, runA!, runB!),
    enabled: runA != null && runB != null && runA !== runB,
  });

  if (runs.length < 2) {
    return (
      <div className="card-dark p-6 text-sm text-slate-500 font-mono">
        Need at least 2 completed runs to compare. Run generation with different profiles to compare.
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
                {r.template_profile_id ? ` [P#${r.template_profile_id}]` : " [default]"}
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
                {r.template_profile_id ? ` [P#${r.template_profile_id}]` : " [default]"}
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
            <h3 className="text-sm font-mono text-slate-400">
              Run #{comparison.run_a.id} vs #{comparison.run_b.id}
            </h3>
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
                {[
                  { label: "Doc Count", key: "doc_count" },
                  { label: "Duration (ms)", key: "duration_ms" },
                  { label: "Input Tokens", key: "input_tokens" },
                  { label: "Output Tokens", key: "output_tokens" },
                  { label: "Cost (USD)", key: "cost_usd" },
                ].map(({ label, key }) => {
                  const va = comparison.run_a[key];
                  const vb = comparison.run_b[key];
                  const diff = va != null && vb != null ? vb - va : null;
                  return (
                    <tr key={key} className="border-b border-white/[0.04]">
                      <td className="px-4 py-2 text-slate-400">{label}</td>
                      <td className="px-4 py-2 font-mono text-slate-400">{va ?? "-"}</td>
                      <td className="px-4 py-2 font-mono text-slate-400">{vb ?? "-"}</td>
                      <td className={`px-4 py-2 font-mono ${
                        diff == null ? "text-slate-600" :
                        diff > 0 ? "text-amber-400" :
                        diff < 0 ? "text-[#a0e06f]" :
                        "text-slate-600"
                      }`}>
                        {diff == null ? "-" : diff > 0 ? `+${diff}` : String(diff)}
                      </td>
                    </tr>
                  );
                })}
                <tr className="border-b border-white/[0.04]">
                  <td className="px-4 py-2 text-slate-400">Profile</td>
                  <td className="px-4 py-2 font-mono text-slate-400">
                    {comparison.run_a.template_profile_id ?? "default"}
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-400">
                    {comparison.run_b.template_profile_id ?? "default"}
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-600">
                    {comparison.run_a.template_profile_id === comparison.run_b.template_profile_id
                      ? "same"
                      : "different"}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Template diffs */}
          {comparison.total_template_diffs > 0 && (
            <div className="px-4 pb-4">
              <h4 className="text-xs font-mono text-slate-500 uppercase mb-2">
                Template Differences ({comparison.total_template_diffs})
              </h4>
              <div className="space-y-1">
                {comparison.template_diffs.map((d: { slug: string; version_a: number | null; version_b: number | null }) => (
                  <div key={d.slug} className="flex items-center justify-between px-3 py-1.5 bg-white/[0.02] rounded text-xs">
                    <span className="font-mono text-slate-400">{d.slug}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-slate-500">v{d.version_a ?? "?"}</span>
                      <span className="text-slate-600">-&gt;</span>
                      <span className="text-accent-primary/60">v{d.version_b ?? "?"}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
