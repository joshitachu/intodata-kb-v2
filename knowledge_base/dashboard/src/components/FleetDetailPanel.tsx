import { useState } from "react";
import type React from "react";
import type { ClientSnapshot, FleetProject, ModelStat } from "../api/fleet";
import { useTriggerGenerate, useTriggerScan } from "../hooks/useFleet";
import { timeAgo, formatAbsoluteDate } from "../lib/timeago";

// ── Shared helpers ────────────────────────────────────────────────────────────

function clientStatus(snapshot: ClientSnapshot): "online" | "stale" | "offline" {
  const diff = Date.now() - new Date(snapshot.sent_at).getTime();
  if (diff < 5 * 60 * 1000) return "online";
  if (diff < 15 * 60 * 1000) return "stale";
  return "offline";
}

function StatusPill({ status }: { status: "online" | "stale" | "offline" }) {
  const styles = {
    online: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    stale: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    offline: "bg-red-500/10 text-red-400 border-red-500/20",
  };
  return (
    <span className={`px-2 py-0.5 text-[10px] font-mono uppercase rounded border ${styles[status]}`}>
      {status}
    </span>
  );
}

function MetricBlock({ label, value, sub }: { label: string; value: string | number | React.ReactNode; sub?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">{label}</span>
      <span className="text-sm font-semibold text-slate-100">{value}</span>
      {sub && <span className="text-[10px] text-slate-600">{sub}</span>}
    </div>
  );
}

// ── Mini bar chart (SVG, no deps) ─────────────────────────────────────────────

function BarChart({
  data,
  height = 48,
  color = "#6366f1",
}: {
  data: { label: string; count: number }[];
  height?: number;
  color?: string;
}) {
  const max = Math.max(...data.map((d) => d.count), 1);
  const width = 100 / data.length;

  return (
    <div className="w-full">
      <svg viewBox={`0 0 100 ${height}`} className="w-full" preserveAspectRatio="none">
        {data.map((d, i) => {
          const h = (d.count / max) * (height - 4);
          return (
            <rect
              key={i}
              x={i * width + 0.5}
              y={height - h - 2}
              width={width - 1}
              height={Math.max(h, 1)}
              fill={color}
              opacity={d.count === 0 ? 0.15 : 0.7}
              rx="1"
            />
          );
        })}
      </svg>
      <div className="flex justify-between mt-1">
        <span className="text-[9px] text-slate-600 font-mono">{data[0]?.label ?? ""}</span>
        <span className="text-[9px] text-slate-600 font-mono">{data[data.length - 1]?.label ?? ""}</span>
      </div>
    </div>
  );
}

// ── Tab: Overview ─────────────────────────────────────────────────────────────

function OverviewTab({ snapshot }: { snapshot: ClientSnapshot }) {
  const st = clientStatus(snapshot);
  const { totals, rag, _mc_source_ip } = snapshot;

  return (
    <div className="space-y-6">
      {/* Identity */}
      <div className="grid grid-cols-2 gap-3 p-4 bg-white/[0.03] rounded-lg border border-white/[0.06]">
        <MetricBlock label="Environment" value={snapshot.environment} />
        <MetricBlock label="Status" value={<StatusPill status={st} />} />
        <MetricBlock
          label="IP Address"
          value={_mc_source_ip ?? "—"}
          sub="source IP from last heartbeat"
        />
        <MetricBlock
          label="Last Seen"
          value={timeAgo(snapshot.sent_at)}
          sub={formatAbsoluteDate(snapshot.sent_at)}
        />
        {snapshot.mcp_url && (
          <div className="col-span-2">
            <span className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">URL</span>
            <a
              href={snapshot.mcp_url}
              target="_blank"
              rel="noopener noreferrer"
              className="block text-sm text-indigo-400 hover:text-indigo-300 truncate mt-0.5"
            >
              {snapshot.mcp_url}
            </a>
          </div>
        )}
      </div>

      {/* Services */}
      <div>
        <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono mb-2">Services</p>
        <div className="flex flex-wrap gap-2">
          {Object.entries(snapshot.health).map(([svc, s]) => (
            <span
              key={svc}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-mono border ${
                s === "ok"
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : "bg-red-500/10 text-red-400 border-red-500/20"
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${s === "ok" ? "bg-emerald-400" : "bg-red-500"}`} />
              {svc}
            </span>
          ))}
        </div>
      </div>

      {/* Intodoc metrics */}
      <div>
        <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono mb-2">Intodoc</p>
        <div className="grid grid-cols-4 gap-3">
          <MetricBlock label="Projects" value={totals.project_count} />
          <MetricBlock label="Docs" value={totals.doc_count} />
          <MetricBlock label="Jobs" value={totals.job_count} />
          <MetricBlock label="Runs" value={totals.generate_run_count} />
          <MetricBlock label="Total cost" value={`$${totals.cost_usd.toFixed(2)}`} />
          <MetricBlock
            label="Avg quality"
            value={totals.latest_run_average_quality != null ? totals.latest_run_average_quality.toFixed(2) : "—"}
          />
          <MetricBlock label="Input tok" value={`${(totals.input_tokens / 1_000_000).toFixed(1)}M`} />
          <MetricBlock label="Output tok" value={`${(totals.output_tokens / 1_000_000).toFixed(1)}M`} />
        </div>
      </div>

      {/* RAG / chat metrics */}
      {rag && (
        <div>
          <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono mb-2">RAG / Chat</p>
          <div className="grid grid-cols-4 gap-3">
            <MetricBlock label="Users" value={rag.user_count} />
            <MetricBlock label="Active 7d" value={rag.active_users_7d} />
            <MetricBlock label="Active 30d" value={rag.active_users_30d} />
            <MetricBlock label="Queries today" value={rag.queries_today} />
            <MetricBlock label="Total queries" value={rag.total_queries} />
            <MetricBlock label="Conversations" value={rag.conversation_count} />
            <MetricBlock
              label="Avg msgs/conv"
              value={rag.avg_queries_per_conversation}
            />
            <MetricBlock
              label="Peak hour"
              value={rag.peak_hour != null ? `${rag.peak_hour}:00` : "—"}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Tab: Projects ─────────────────────────────────────────────────────────────

function ProjectsTab({ snapshot }: { snapshot: ClientSnapshot }) {
  const scan = useTriggerScan();
  const generate = useTriggerGenerate();
  const slug = snapshot.client_slug;

  const statusColor: Record<string, string> = {
    ready: "text-emerald-400",
    scanning: "text-sky-400",
    generating: "text-violet-400",
    error: "text-red-400",
    pending: "text-slate-500",
  };

  return (
    <div className="space-y-2">
      {snapshot.projects.length === 0 && (
        <p className="text-sm text-slate-500 py-8 text-center">No projects registered on this instance.</p>
      )}
      {snapshot.projects.map((p: FleetProject) => {
        const busy = scan.isPending || generate.isPending || ["scanning", "generating"].includes(p.status);
        return (
          <div
            key={p.id}
            className="p-3 rounded-lg bg-white/[0.03] border border-white/[0.06] hover:border-white/[0.1] transition-all"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-mono text-slate-200 truncate">{p.display_name}</p>
                <p className="text-[10px] text-slate-500 mt-0.5">
                  {p.platform} &middot; {p.doc_count ?? 0} docs &middot; {p.job_count ?? 0} jobs
                  {p.last_scan_at && ` · scanned ${timeAgo(p.last_scan_at)}`}
                  {p.last_generate_at && ` · generated ${timeAgo(p.last_generate_at)}`}
                </p>
                {p.error_summary && (
                  <p className="text-[10px] text-red-400 mt-1">{p.error_summary}</p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className={`text-[10px] font-mono uppercase ${statusColor[p.status] ?? "text-slate-500"}`}>
                  {p.status}
                </span>
                <button
                  disabled={busy}
                  onClick={() => scan.mutate({ clientSlug: slug, projectId: p.id })}
                  className="px-2 py-1 text-[10px] font-mono rounded border border-white/[0.1] text-slate-400 hover:text-slate-200 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                >
                  Scan
                </button>
                <button
                  disabled={busy}
                  onClick={() => generate.mutate({ clientSlug: slug, projectId: p.id })}
                  className="px-2 py-1 text-[10px] font-mono rounded border border-indigo-500/30 text-indigo-400 hover:text-indigo-300 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                >
                  Generate
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Tab: Activity ─────────────────────────────────────────────────────────────

function ActivityTab({ snapshot }: { snapshot: ClientSnapshot }) {
  const { rag } = snapshot;

  if (!rag) {
    return (
      <div className="flex items-center justify-center h-40">
        <p className="text-slate-500 text-sm">RAG metrics not available — check MCP_INGEST_API_KEY on this VPS.</p>
      </div>
    );
  }

  const dayLabels = rag.queries_7d.map((d) => d.date.slice(5)); // MM-DD
  const hourLabels = rag.queries_24h.map((d) => ({ ...d, label: `${d.hour}h` }));

  return (
    <div className="space-y-6">
      <div className="p-4 bg-white/[0.03] rounded-lg border border-white/[0.06]">
        <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono mb-3">
          Queries — last 7 days
        </p>
        <BarChart
          data={rag.queries_7d.map((d, i) => ({ label: dayLabels[i], count: d.count }))}
          height={56}
          color="#6366f1"
        />
        <div className="flex justify-between mt-3">
          {rag.queries_7d.map((d, i) => (
            <div key={i} className="text-center">
              <p className="text-[10px] text-slate-300 font-mono">{d.count}</p>
              <p className="text-[9px] text-slate-600">{dayLabels[i]}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="p-4 bg-white/[0.03] rounded-lg border border-white/[0.06]">
        <div className="flex items-center justify-between mb-3">
          <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">
            Queries by hour — last 24h
          </p>
          {rag.peak_hour != null && (
            <span className="text-[10px] font-mono text-amber-400">
              peak {rag.peak_hour}:00
            </span>
          )}
        </div>
        <BarChart
          data={hourLabels.map((d) => ({ label: d.label, count: d.count }))}
          height={48}
          color="#f59e0b"
        />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <MetricBlock label="Total queries" value={rag.total_queries} />
        <MetricBlock label="Conversations" value={rag.conversation_count} />
        <MetricBlock label="Avg msgs/conv" value={rag.avg_queries_per_conversation} />
      </div>
    </div>
  );
}

// ── Tab: Benchmark ────────────────────────────────────────────────────────────

function BenchmarkTab({ snapshot }: { snapshot: ClientSnapshot }) {
  const stats = snapshot.model_stats;

  if (!stats || stats.length === 0) {
    return (
      <div className="flex items-center justify-center h-40">
        <p className="text-slate-500 text-sm">No generation runs recorded yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">
        Model performance — based on last {snapshot.latest_generate_runs.length} runs
      </p>
      <div className="overflow-hidden rounded-lg border border-white/[0.06]">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-white/[0.06] bg-white/[0.03]">
              <th className="text-left px-3 py-2 text-slate-500 font-mono font-normal">Model</th>
              <th className="text-right px-3 py-2 text-slate-500 font-mono font-normal">Runs</th>
              <th className="text-right px-3 py-2 text-slate-500 font-mono font-normal">Avg quality</th>
              <th className="text-right px-3 py-2 text-slate-500 font-mono font-normal">Avg cost</th>
              <th className="text-right px-3 py-2 text-slate-500 font-mono font-normal">Avg time</th>
            </tr>
          </thead>
          <tbody>
            {(stats as ModelStat[])
              .sort((a, b) => (b.avg_quality ?? 0) - (a.avg_quality ?? 0))
              .map((s, i) => (
                <tr
                  key={s.model}
                  className={`border-b border-white/[0.04] last:border-0 ${i % 2 === 0 ? "" : "bg-white/[0.02]"}`}
                >
                  <td className="px-3 py-2.5 font-mono text-slate-300 truncate max-w-[180px]">{s.model}</td>
                  <td className="px-3 py-2.5 text-right text-slate-400">{s.run_count}</td>
                  <td className="px-3 py-2.5 text-right">
                    {s.avg_quality != null ? (
                      <span className={s.avg_quality >= 4 ? "text-emerald-400" : s.avg_quality >= 3 ? "text-amber-400" : "text-red-400"}>
                        {s.avg_quality.toFixed(2)}
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-2.5 text-right text-slate-400">
                    {s.avg_cost_usd != null ? `$${s.avg_cost_usd.toFixed(3)}` : "—"}
                  </td>
                  <td className="px-3 py-2.5 text-right text-slate-400">
                    {s.avg_duration_ms != null ? `${(s.avg_duration_ms / 60000).toFixed(1)}m` : "—"}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

type Tab = "overview" | "projects" | "activity" | "benchmark";

interface Props {
  slug: string;
  snapshot: ClientSnapshot;
  onClose: () => void;
}

export default function FleetDetailPanel({ slug, snapshot, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("overview");

  const tabs: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "projects", label: `Projects (${snapshot.projects.length})` },
    { key: "activity", label: "Activity" },
    { key: "benchmark", label: "Benchmark" },
  ];

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed top-0 right-0 h-full w-full max-w-xl bg-[#0f1117] border-l border-white/[0.08] z-50 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between px-5 pt-5 pb-4 border-b border-white/[0.06] shrink-0">
          <div>
            <h2 className="text-base font-semibold text-slate-100">{slug}</h2>
            <p className="text-[10px] text-slate-500 font-mono mt-0.5">
              {snapshot._mc_source_ip ?? "IP unknown"} &middot; {snapshot.environment}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-slate-300 transition-colors text-lg leading-none mt-0.5"
          >
            ×
          </button>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 px-5 pt-3 pb-0 border-b border-white/[0.06] shrink-0">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-3 py-1.5 text-xs rounded-t transition-all ${
                tab === t.key
                  ? "text-slate-200 bg-white/[0.06] border-b-2 border-indigo-400"
                  : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-5">
          {tab === "overview" && <OverviewTab snapshot={snapshot} />}
          {tab === "projects" && <ProjectsTab snapshot={snapshot} />}
          {tab === "activity" && <ActivityTab snapshot={snapshot} />}
          {tab === "benchmark" && <BenchmarkTab snapshot={snapshot} />}
        </div>
      </div>
    </>
  );
}
