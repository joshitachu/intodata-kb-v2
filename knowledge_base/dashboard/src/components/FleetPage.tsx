import { useState } from "react";
import type { ClientSnapshot, FleetProject } from "../api/fleet";
import { useFleet, useTriggerGenerate, useTriggerScan } from "../hooks/useFleet";
import { timeAgo } from "../lib/timeago";
import FleetDetailPanel from "./FleetDetailPanel";

// ── Helpers ───────────────────────────────────────────────────────────────────

function clientStatus(snapshot: ClientSnapshot): "online" | "stale" | "offline" {
  const diff = Date.now() - new Date(snapshot.sent_at).getTime();
  if (diff < 5 * 60 * 1000) return "online";
  if (diff < 15 * 60 * 1000) return "stale";
  return "offline";
}

function StatusDot({ status }: { status: "online" | "stale" | "offline" }) {
  const colors = {
    online: "bg-emerald-400",
    stale: "bg-amber-400",
    offline: "bg-red-500",
  };
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${colors[status]}`}
      title={status}
    />
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">{label}</span>
      <span className="text-sm font-medium text-slate-200">{value}</span>
    </div>
  );
}

// ── Project row inside a client card ─────────────────────────────────────────

function ProjectRow({
  project,
  clientSlug,
}: {
  project: FleetProject;
  clientSlug: string;
}) {
  const scan = useTriggerScan();
  const generate = useTriggerGenerate();
  const isBusy =
    scan.isPending ||
    generate.isPending ||
    ["scanning", "generating"].includes(project.status);

  const statusColor: Record<string, string> = {
    ready: "text-emerald-400",
    scanning: "text-sky-400",
    generating: "text-violet-400",
    error: "text-red-400",
    pending: "text-slate-500",
  };

  return (
    <div className="flex items-center justify-between py-2 border-b border-white/[0.04] last:border-0 gap-4">
      <div className="flex-1 min-w-0">
        <p className="text-xs font-mono text-slate-300 truncate">{project.display_name}</p>
        <p className="text-[10px] text-slate-600 uppercase tracking-wide">
          {project.platform} &middot; {project.doc_count ?? 0} docs &middot; {project.job_count ?? 0} jobs
        </p>
        {project.error_summary && (
          <p className="text-[10px] text-red-400 mt-0.5 truncate">{project.error_summary}</p>
        )}
      </div>

      <span className={`text-[10px] font-mono uppercase ${statusColor[project.status] ?? "text-slate-500"}`}>
        {project.status}
      </span>

      <div className="flex items-center gap-1.5 shrink-0">
        <button
          disabled={isBusy}
          onClick={() => scan.mutate({ clientSlug, projectId: project.id })}
          className="px-2 py-1 text-[10px] font-mono rounded border border-white/[0.1] text-slate-400 hover:text-slate-200 hover:border-white/[0.2] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          {scan.isPending ? "..." : "Scan"}
        </button>
        <button
          disabled={isBusy}
          onClick={() => generate.mutate({ clientSlug, projectId: project.id })}
          className="px-2 py-1 text-[10px] font-mono rounded border border-indigo-500/30 text-indigo-400 hover:text-indigo-300 hover:border-indigo-400/50 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          {generate.isPending ? "..." : "Generate"}
        </button>
      </div>
    </div>
  );
}

// ── Client card ───────────────────────────────────────────────────────────────

function ClientCard({ slug, snapshot, onSelect }: { slug: string; snapshot: ClientSnapshot; onSelect: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const st = clientStatus(snapshot);
  const { totals, projects } = snapshot;

  const errorProjects = projects.filter((p) => p.status === "error" || p.error_summary);
  const hasErrors = errorProjects.length > 0;

  return (
    <div className="bg-surface-card border border-white/[0.06] rounded-xl p-5 space-y-4 hover:border-white/[0.1] transition-all cursor-pointer" onClick={onSelect}>
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <StatusDot status={st} />
          <div>
            <h3 className="text-sm font-semibold text-slate-100 leading-tight">{slug}</h3>
            <p className="text-[10px] text-slate-500 font-mono mt-0.5">
              {snapshot.environment} &middot; last seen {timeAgo(snapshot.sent_at)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {hasErrors && (
            <span className="px-1.5 py-0.5 text-[10px] font-mono rounded bg-red-500/10 text-red-400 border border-red-500/20">
              {errorProjects.length} error{errorProjects.length > 1 ? "s" : ""}
            </span>
          )}
          {snapshot.mcp_url && (
            <a
              href={snapshot.mcp_url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-2 py-1 text-[10px] font-mono rounded border border-white/[0.1] text-slate-500 hover:text-slate-300 hover:border-white/[0.2] transition-all"
            >
              Open &rarr;
            </a>
          )}
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-4 gap-3 pt-1">
        <Stat label="Projects" value={totals.project_count} />
        <Stat label="Docs" value={totals.doc_count} />
        <Stat label="Cost" value={`$${totals.cost_usd.toFixed(2)}`} />
        <Stat
          label="Quality"
          value={totals.latest_run_average_quality != null ? totals.latest_run_average_quality.toFixed(2) : "—"}
        />
      </div>

      <div className="grid grid-cols-4 gap-3">
        <Stat label="Jobs" value={totals.job_count} />
        <Stat label="Runs" value={totals.generate_run_count} />
        <Stat label="Input tok" value={(totals.input_tokens / 1_000_000).toFixed(1) + "M"} />
        <Stat label="Output tok" value={(totals.output_tokens / 1_000_000).toFixed(1) + "M"} />
      </div>

      {/* Service health */}
      <div className="flex items-center gap-3 pt-1 border-t border-white/[0.04]">
        {Object.entries(snapshot.health).map(([svc, s]) => (
          <span key={svc} className="flex items-center gap-1.5 text-[10px] font-mono text-slate-500">
            <span className={`w-1.5 h-1.5 rounded-full ${s === "ok" ? "bg-emerald-400" : "bg-red-500"}`} />
            {svc}
          </span>
        ))}
      </div>

      {/* Project list (collapsible) */}
      {projects.length > 0 && (
        <div className="border-t border-white/[0.04] pt-3" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-[10px] font-mono uppercase tracking-widest text-slate-500 hover:text-slate-300 transition-colors"
          >
            [{expanded ? "hide" : "show"} {projects.length} project{projects.length > 1 ? "s" : ""}]
          </button>
          {expanded && (
            <div className="mt-2">
              {projects.map((p) => (
                <ProjectRow key={p.id} project={p} clientSlug={slug} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Fleet summary bar ─────────────────────────────────────────────────────────

function FleetSummary({ snapshots }: { snapshots: Record<string, ClientSnapshot> }) {
  const clients = Object.values(snapshots);
  const online = clients.filter((c) => clientStatus(c) === "online").length;
  const totalCost = clients.reduce((s, c) => s + c.totals.cost_usd, 0);
  const totalDocs = clients.reduce((s, c) => s + c.totals.doc_count, 0);
  const totalProjects = clients.reduce((s, c) => s + c.totals.project_count, 0);

  return (
    <div className="grid grid-cols-4 gap-4 p-4 bg-surface-card border border-white/[0.06] rounded-xl">
      <Stat label="Instances" value={`${online} / ${clients.length} online`} />
      <Stat label="Total projects" value={totalProjects} />
      <Stat label="Total docs" value={totalDocs} />
      <Stat label="Total cost" value={`$${totalCost.toFixed(2)}`} />
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function FleetPage() {
  const { data: snapshots, isLoading, error, dataUpdatedAt } = useFleet();
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-48">
        <p className="text-slate-500 text-sm font-mono">Connecting to Mission Control...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-48">
        <p className="text-red-400 text-sm font-mono">
          Mission Control unreachable: {(error as Error).message}
        </p>
      </div>
    );
  }

  const entries = Object.entries(snapshots ?? {});

  return (
    <div className="space-y-6 relative">
      {selectedSlug && snapshots?.[selectedSlug] && (
        <FleetDetailPanel
          slug={selectedSlug}
          snapshot={snapshots[selectedSlug]}
          onClose={() => setSelectedSlug(null)}
        />
      )}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-100">Fleet</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            {entries.length} instance{entries.length !== 1 ? "s" : ""} &middot; refreshes every 30s
            {dataUpdatedAt ? ` &middot; last updated ${timeAgo(new Date(dataUpdatedAt).toISOString())}` : ""}
          </p>
        </div>
      </div>

      {entries.length === 0 ? (
        <div className="flex items-center justify-center h-40 border border-dashed border-white/[0.1] rounded-xl">
          <p className="text-slate-500 text-sm">No instances reporting yet.</p>
        </div>
      ) : (
        <>
          <FleetSummary snapshots={snapshots!} />
          <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
            {entries.map(([slug, snapshot]) => (
              <ClientCard key={slug} slug={slug} snapshot={snapshot} onSelect={() => setSelectedSlug(slug)} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
