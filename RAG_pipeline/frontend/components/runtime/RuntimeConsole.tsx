"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import {
  Activity, AlertTriangle, ArrowRight, CircleAlert, Loader2, RefreshCw, Server, X,
} from "lucide-react";
import {
  useRuntimeExecution, useRuntimeExecutions, useRuntimeHealth, useRuntimeIncidents,
  useRuntimeIncident, useRuntimeJobs, useRuntimeOverview, useRuntimePlans,
  useRuntimeSources, useRuntimeEngines,
} from "@/hooks/useRuntimeLogs";
import { useUserProjects } from "@/hooks/useUserProjects";
import { useChatContext } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";
import type {
  RuntimeExecution, RuntimeIncidentInsight, RuntimeJob, RuntimePlan, RuntimeSource,
  RuntimeEngine,
} from "@/types";

export type RuntimeConsoleView = "hub" | "executions" | "jobs" | "plans" | "incidents" | "estate";

const PAGE_SIZE = 50;
const NAV: Array<{ view: RuntimeConsoleView; href: string; label: string }> = [
  { view: "hub", href: "/admin/logs", label: "Overview" },
  { view: "executions", href: "/admin/logs/executions", label: "Runs" },
  { view: "jobs", href: "/admin/logs/jobs", label: "Jobs" },
  { view: "plans", href: "/admin/logs/plans", label: "Plans" },
  { view: "incidents", href: "/admin/logs/incidents", label: "Incidents" },
  { view: "estate", href: "/admin/logs/estate", label: "Estate" },
];

type Detail =
  | { kind: "execution"; item: RuntimeExecution }
  | { kind: "incident"; item: RuntimeIncidentInsight }
  | { kind: "job"; item: RuntimeJob }
  | { kind: "plan"; item: RuntimePlan }
  | { kind: "source"; item: RuntimeSource }
  | { kind: "engine"; item: RuntimeEngine };

export function RuntimeConsole({ view }: { view: RuntimeConsoleView }) {
  const [projectId, setProjectId] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const projects = useUserProjects();
  const scope = projectId || undefined;

  return (
    <section className="mx-auto flex w-full max-w-7xl flex-col gap-6 overflow-x-hidden pb-8">
      <header className="flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-sm font-medium text-muted-foreground">Runtime operations</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-normal text-foreground">Operational console</h1>
        </div>
        <label className="flex min-w-0 flex-col gap-1 text-sm text-muted-foreground sm:w-64">
          Project scope
          <select value={projectId} onChange={(event) => setProjectId(event.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground">
            <option value="">All permitted projects</option>
            {projects.data?.projects.map((project) => <option key={project.id} value={project.id}>{project.display_name}</option>)}
          </select>
        </label>
      </header>
      <nav aria-label="Runtime views" className="-mx-1 flex gap-1 overflow-x-auto border-b border-border px-1">
        {NAV.map((item) => <Link key={item.view} href={item.href} className={cn("shrink-0 border-b-2 px-3 py-2 text-sm font-medium transition-colors motion-reduce:transition-none", item.view === view ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>{item.label}</Link>)}
      </nav>
      {view === "hub" && <Hub scope={scope} onDetail={setDetail} />}
      {view === "executions" && <Executions scope={scope} onDetail={setDetail} />}
      {view === "jobs" && <Jobs scope={scope} onDetail={setDetail} />}
      {view === "plans" && <Plans scope={scope} onDetail={setDetail} />}
      {view === "incidents" && <Incidents scope={scope} onDetail={setDetail} />}
      {view === "estate" && <Estate scope={scope} onDetail={setDetail} />}
      <DetailDrawer detail={detail} onClose={() => setDetail(null)} />
    </section>
  );
}

function Hub({ scope, onDetail }: { scope?: string; onDetail: (detail: Detail) => void }) {
  const overview = useRuntimeOverview({ projectId: scope });
  const runs = useRuntimeExecutions({ projectId: scope, limit: 12 });
  const health = useRuntimeHealth({ projectId: scope, limit: 6 });
  const incidents = useRuntimeIncidents({ projectId: scope, limit: 6 });
  const estate = overview.data?.overview;
  const failedRuns = runs.data?.executions.filter((run) => run.state === "failed") ?? [];
  const trend = runs.data?.executions ?? [];

  return <>
    <AskLogs scope={scope} />
    <SyncStatusBanner scope={scope} />
    <QueryState queries={[overview, runs, health, incidents]}>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon={<Server />} label="Engines online" value={`${estate?.engines_online ?? 0}/${estate?.engines_total ?? 0}`} tone="good" />
        <Kpi icon={<CircleAlert />} label="Unhealthy components" value={String((estate?.engines_unhealthy ?? 0) + (estate?.jobs_unhealthy ?? 0) + (estate?.plans_unhealthy ?? 0))} tone="warning" />
        <Kpi icon={<Activity />} label="Runs observed" value={String(runs.data?.executions.length ?? 0)} hint="current window" />
        <Kpi icon={<AlertTriangle />} label="Open signals" value={String((incidents.data?.incidents.length ?? 0) + failedRuns.length)} tone="danger" />
      </div>
      <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <Panel title="Outcome trend" action={<Link href="/admin/logs/executions" className="text-sm text-primary hover:underline">View runs</Link>}>
          {trend.length === 0 ? <Empty label="No runs are available for this scope." /> : <div className="flex h-32 items-end gap-1.5" aria-label="Recent run outcomes">
            {trend.slice().reverse().map((run) => <button key={run.execution_id} onClick={() => onDetail({ kind: "execution", item: run })} className={cn("min-w-2 flex-1 rounded-t-sm transition-opacity hover:opacity-75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", run.state === "success" ? "bg-emerald-500" : run.state === "failed" ? "bg-destructive" : "bg-amber-500")} style={{ height: `${run.state === "failed" ? 100 : run.state === "running" ? 72 : 48}%` }} title={`${run.job_name}: ${run.state}`}><span className="sr-only">{run.job_name}: {run.state}</span></button>)}
          </div>}
        </Panel>
        <Panel title="Needs attention" action={<Link href="/admin/logs/incidents" className="text-sm text-primary hover:underline">View incidents</Link>}>
          <div className="divide-y divide-border">
            {[...incidents.data?.incidents ?? [], ...failedRuns.map((run) => ({ incident_id: run.execution_id, execution_id: run.execution_id, job_name: run.job_name, symptom_summary: "Execution failed", state: run.state, project_id: "", next_checks: [], estate_context: {} }))].slice(0, 5).map((item) => <button key={item.incident_id} onClick={() => "project_id" in item && item.project_id ? onDetail({ kind: "incident", item: item as RuntimeIncidentInsight }) : onDetail({ kind: "execution", item: failedRuns.find((run) => run.execution_id === item.execution_id)! })} className="flex w-full items-start gap-3 py-3 text-left hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" /><span className="min-w-0"><span className="block truncate text-sm font-medium">{item.job_name ?? item.execution_id}</span><span className="block truncate text-xs text-muted-foreground">{item.symptom_summary ?? "Runtime attention required"}</span></span>
            </button>)}
            {!incidents.data?.incidents.length && !failedRuns.length && <Empty label="No active attention signals." />}
          </div>
        </Panel>
      </div>
      <Panel title="Recent runs" action={<Link href="/admin/logs/executions" className="text-sm text-primary hover:underline">All runs</Link>}><RunTable rows={runs.data?.executions ?? []} onDetail={onDetail} compact /></Panel>
      <Panel title="Unhealthy jobs"><HealthList items={health.data?.health ?? []} /></Panel>
    </QueryState>
  </>;
}

function Executions({ scope, onDetail }: { scope?: string; onDetail: (detail: Detail) => void }) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [scope]);
  const query = useRuntimeExecutions({ projectId: scope, limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  return <QueryState queries={[query]}><Panel title="Runs" description="One row per execution. Job-run mirrors are intentionally not shown."><PaginatedTable rows={query.data?.executions ?? []} total={query.data?.total ?? 0} page={page} onPage={setPage} render={(rows) => <RunTable rows={rows} onDetail={onDetail} />} /></Panel></QueryState>;
}

function Jobs({ scope, onDetail }: { scope?: string; onDetail: (detail: Detail) => void }) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [scope]);
  const query = useRuntimeJobs({ projectId: scope, limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  return <QueryState queries={[query]}><Panel title="Jobs"><PaginatedTable rows={query.data?.jobs ?? []} total={query.data?.total ?? 0} page={page} onPage={setPage} render={(rows) => <EntityTable headers={["Job", "Environment", "Engine", "Status"]} rows={rows} rowKey={(job) => job.job_id} onClick={(job) => onDetail({ kind: "job", item: job })} cells={(job) => [job.display_name ?? job.name, job.environment_name ?? "-", job.engine_name ?? "-", job.status ?? "unknown"]} />} /></Panel></QueryState>;
}

function Plans({ scope, onDetail }: { scope?: string; onDetail: (detail: Detail) => void }) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [scope]);
  const query = useRuntimePlans({ projectId: scope, limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  return <QueryState queries={[query]}><Panel title="Plans"><PaginatedTable rows={query.data?.plans ?? []} total={query.data?.total ?? 0} page={page} onPage={setPage} render={(rows) => <EntityTable headers={["Plan", "Environment", "Steps", "Status"]} rows={rows} rowKey={(plan) => plan.plan_id} onClick={(plan) => onDetail({ kind: "plan", item: plan })} cells={(plan) => [plan.display_name ?? plan.name, plan.environment_name ?? "-", String(plan.step_count ?? plan.steps.length), plan.status ?? "unknown"]} />} /></Panel></QueryState>;
}

function Incidents({ scope, onDetail }: { scope?: string; onDetail: (detail: Detail) => void }) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [scope]);
  const query = useRuntimeIncidents({ projectId: scope, limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  return <QueryState queries={[query]}><Panel title="Incidents"><PaginatedTable rows={query.data?.incidents ?? []} total={query.data?.total ?? 0} page={page} onPage={setPage} render={(rows) => <EntityTable headers={["Job", "Symptom", "Confidence", "Last bad"]} rows={rows} rowKey={(incident) => incident.incident_id} onClick={(incident) => onDetail({ kind: "incident", item: incident })} cells={(incident) => [incident.job_name ?? incident.execution_id, incident.symptom_summary ?? "-", incident.confidence ?? "-", formatDate(incident.first_bad_at)]} />} /></Panel></QueryState>;
}

function Estate({ scope, onDetail }: { scope?: string; onDetail: (detail: Detail) => void }) {
  const sources = useRuntimeSources({ projectId: scope, limit: 100 });
  const engines = useRuntimeEngines({ projectId: scope, limit: 100 });
  return <QueryState queries={[sources, engines]}><div className="grid gap-6 xl:grid-cols-2"><Panel title="Runtime sources"><EntityTable headers={["Source", "Type", "Status", "Last sync"]} rows={sources.data?.sources ?? []} rowKey={(source) => `${source.project_id}:${source.source_key}`} onClick={(source) => onDetail({ kind: "source", item: source })} cells={(source) => [source.label, source.source_type, source.status, formatDate(source.last_synced_at)]} /></Panel><Panel title="Engines"><EntityTable headers={["Engine", "Environment", "Version", "Status"]} rows={engines.data?.engines ?? []} rowKey={(engine) => engine.engine_id} onClick={(engine) => onDetail({ kind: "engine", item: engine })} cells={(engine) => [engine.name, engine.environment_name ?? "-", engine.version ?? "-", engine.status]} /></Panel></div></QueryState>;
}

function AskLogs({ scope }: { scope?: string }) {
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const chat = useChatContext();
  const router = useRouter();
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!question.trim() || sending) return;
    setSending(true);
    await chat.handleSendMessage({ query: question.trim(), project_id: scope });
    router.push("/");
  }
  return <form onSubmit={submit} className="flex flex-col gap-3 border border-border bg-muted/35 p-4 sm:flex-row sm:items-end">
    <label className="min-w-0 flex-1 text-sm font-medium">Ask these logs<input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="What changed in the failed runs?" className="mt-1 h-10 w-full rounded-md border border-input bg-background px-3 text-sm font-normal" /></label>
    <button type="submit" disabled={!question.trim() || sending} className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50"><ArrowRight className="h-4 w-4" />{sending ? "Opening chat" : "Ask Nexus"}</button>
  </form>;
}

function DetailDrawer({ detail, onClose }: { detail: Detail | null; onClose: () => void }) {
  const execution = useRuntimeExecution(detail?.kind === "execution" ? detail.item.execution_id : null);
  const incident = useRuntimeIncident(detail?.kind === "incident" ? detail.item.incident_id : null);
  if (!detail) return null;
  const item = detail.kind === "execution" ? execution.data?.execution ?? detail.item : detail.kind === "incident" ? incident.data?.incident ?? detail.item : detail.item;
  return <div className="fixed inset-0 z-50 flex justify-end bg-foreground/20 p-0 sm:p-4" role="presentation" onMouseDown={onClose}><aside role="dialog" aria-modal="true" aria-label="Runtime details" onMouseDown={(event) => event.stopPropagation()} className="flex h-full w-full max-w-xl flex-col border-l border-border bg-background shadow-xl sm:border sm:shadow-2xl">
    <div className="flex items-start justify-between border-b border-border p-5"><div><p className="text-xs font-medium uppercase text-muted-foreground">{detail.kind}</p><h2 className="mt-1 text-lg font-semibold">{detailTitle(detail, item)}</h2></div><button onClick={onClose} className="rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Close details"><X className="h-4 w-4" /></button></div>
    <div className="min-h-0 flex-1 overflow-y-auto p-5"><DetailFields item={item as Record<string, unknown>} /></div>
  </aside></div>;
}

function DetailFields({ item }: { item: Record<string, unknown> }) {
  return <dl className="grid gap-4 sm:grid-cols-2">{Object.entries(item).filter(([, value]) => value !== null && value !== undefined && value !== "" && !Array.isArray(value) && typeof value !== "object").map(([key, value]) => <div key={key} className="border-b border-border pb-3"><dt className="text-xs font-medium uppercase text-muted-foreground">{key.replaceAll("_", " ")}</dt><dd className="mt-1 break-words text-sm text-foreground">{String(value)}</dd></div>)}</dl>;
}

function RunTable({ rows, onDetail, compact = false }: { rows: RuntimeExecution[]; onDetail: (detail: Detail) => void; compact?: boolean }) {
  return <EntityTable headers={compact ? ["Run", "Outcome", "Started"] : ["Run", "Job", "Outcome", "Started", "Duration"]} rows={rows} rowKey={(run) => run.execution_id} onClick={(run) => onDetail({ kind: "execution", item: run })} cells={(run) => compact ? [run.job_name, run.state, formatDate(run.started_at)] : [run.execution_id, run.job_name, run.state, formatDate(run.started_at), formatDuration(run.duration_ms)]} />;
}

function EntityTable<T>({ headers, rows, rowKey, onClick, cells }: { headers: string[]; rows: T[]; rowKey: (item: T) => string; onClick: (item: T) => void; cells: (item: T) => string[] }) {
  if (rows.length === 0) return <Empty label="No records are available for this scope." />;
  return <div className="overflow-x-auto"><table className="w-full min-w-[620px] text-left text-sm"><thead className="border-b border-border text-xs uppercase text-muted-foreground"><tr>{headers.map((header) => <th key={header} className="px-3 py-3 font-medium">{header}</th>)}</tr></thead><tbody>{rows.map((item) => <tr key={rowKey(item)} tabIndex={0} onClick={() => onClick(item)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onClick(item); } }} className="cursor-pointer border-b border-border/70 transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none">{cells(item).map((cell, index) => <td key={`${rowKey(item)}:${headers[index]}`} className={cn("max-w-64 truncate px-3 py-3", index === 0 && "font-medium text-foreground", cell === "failed" && "text-destructive", cell === "success" && "text-emerald-600 dark:text-emerald-400")}>{cell}</td>)}</tr>)}</tbody></table></div>;
}

function PaginatedTable<T>({ rows, total, page, onPage, render }: { rows: T[]; total: number; page: number; onPage: (page: number) => void; render: (rows: T[]) => ReactNode }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return <><p className="mb-3 text-sm text-muted-foreground">{total} records</p>{render(rows)}<div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm"><span className="text-muted-foreground">Page {page + 1} of {pages}</span><div className="flex gap-2"><button type="button" disabled={page === 0} onClick={() => onPage(page - 1)} className="rounded-md border border-input px-3 py-1.5 disabled:opacity-50">Previous</button><button type="button" disabled={page + 1 >= pages} onClick={() => onPage(page + 1)} className="rounded-md border border-input px-3 py-1.5 disabled:opacity-50">Next</button></div></div></>;
}

function QueryState({ queries, children }: { queries: Array<{ isLoading: boolean; isError: boolean; refetch: () => unknown }>; children: ReactNode }) {
  if (queries.some((query) => query.isLoading)) return <div className="flex min-h-48 items-center justify-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />Loading runtime data</div>;
  if (queries.some((query) => query.isError)) return <div className="border border-destructive/40 p-5"><p className="font-medium text-destructive">Some runtime data could not be loaded.</p><button onClick={() => queries.forEach((query) => query.refetch())} className="mt-3 inline-flex items-center gap-2 rounded-md border border-input px-3 py-2 text-sm"><RefreshCw className="h-4 w-4" />Retry</button></div>;
  return <>{children}</>;
}

function Panel({ title, description, action, children }: { title: string; description?: string; action?: ReactNode; children: ReactNode }) { return <section className="border border-border bg-card"><div className="flex items-start justify-between gap-4 border-b border-border px-4 py-3"><div><h2 className="font-semibold">{title}</h2>{description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}</div>{action}</div><div className="p-4">{children}</div></section>; }
function Kpi({ icon, label, value, hint, tone = "neutral" }: { icon: ReactNode; label: string; value: string; hint?: string; tone?: "neutral" | "good" | "warning" | "danger" }) { const tones = { neutral: "text-primary", good: "text-emerald-600 dark:text-emerald-400", warning: "text-amber-600 dark:text-amber-400", danger: "text-destructive" }; return <div className="border border-border bg-card p-4"><div className={cn("flex items-center gap-2", tones[tone])}>{icon}<span className="text-sm font-medium text-muted-foreground">{label}</span></div><p className="mt-3 text-2xl font-semibold text-foreground">{value}</p>{hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}</div>; }
function Empty({ label }: { label: string }) { return <p className="py-8 text-center text-sm text-muted-foreground">{label}</p>; }
function HealthList({ items }: { items: Array<{ job_name: string; failure_rate: number; current_failure_streak: number }> }) { return items.length ? <div className="divide-y divide-border">{items.map((item) => <div key={item.job_name} className="flex justify-between gap-3 py-3 text-sm"><span className="truncate font-medium">{item.job_name}</span><span className="shrink-0 text-destructive">{Math.round(item.failure_rate * 100)}% failed</span></div>)}</div> : <Empty label="No unhealthy job rollups are available." />; }
function detailTitle(detail: Detail, item: Record<string, unknown>) { return String(item.execution_id ?? item.incident_id ?? item.job_id ?? item.plan_id ?? item.engine_id ?? item.label ?? detail.kind); }
function formatDate(value: string | null | undefined) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "-"; }

function formatAge(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

// Surfaces automatic-polling health on the Overview: when the last import ran,
// how many sources are polling, and a clear warning when data has gone stale.
function SyncStatusBanner({ scope }: { scope?: string }) {
  const sources = useRuntimeSources({ projectId: scope, limit: 100 });
  const rows = sources.data?.sources ?? [];
  if (sources.isLoading || rows.length === 0) return null;

  const polling = rows.filter((source) => source.enabled !== false);
  const syncedTimes = rows
    .map((source) => source.last_synced_at)
    .filter((value): value is string => Boolean(value))
    .map((value) => new Date(value).getTime());
  const freshest = syncedTimes.length ? Math.max(...syncedTimes) : null;
  const ageMs = freshest === null ? null : Date.now() - freshest;
  const intervals = polling.map((source) => source.sync_interval_seconds ?? 60);
  const minInterval = intervals.length ? Math.min(...intervals) : 60;
  const maxInterval = intervals.length ? Math.max(...intervals) : 60;
  // Stale = older than three missed cycles, floored at 15 min to avoid flapping.
  const staleThresholdMs = Math.max(maxInterval * 3, 900) * 1000;

  const noPolling = polling.length === 0;
  const isStale = noPolling || ageMs === null || ageMs > staleThresholdMs;

  let message: string;
  if (noPolling) {
    message = "No runtime sources are enabled for polling. Enable a source to start importing data.";
  } else if (ageMs === null) {
    message = `No runtime data imported yet. ${polling.length} source(s) set to poll every ${formatInterval(minInterval)}.`;
  } else if (isStale) {
    message = `Last import ${formatAge(ageMs)} ago — data may be stale. ${polling.length} source(s) set to poll every ${formatInterval(minInterval)}.`;
  } else {
    message = `Last import ${formatAge(ageMs)} ago · ${polling.length} source(s) polling every ${formatInterval(minInterval)}.`;
  }

  return (
    <div className={cn(
      "flex flex-col gap-2 rounded-lg border px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between",
      isStale ? "border-amber-500/40 bg-amber-500/5 text-amber-700 dark:text-amber-400" : "border-border bg-muted/30 text-muted-foreground",
    )}>
      <span className="flex items-center gap-2">
        {isStale ? <AlertTriangle className="h-4 w-4 shrink-0" /> : <RefreshCw className="h-4 w-4 shrink-0" />}
        <span>{message}</span>
      </span>
      <Link href="/admin/runtime" className="shrink-0 font-medium text-primary hover:underline">Manage sources</Link>
    </div>
  );
}
function formatDuration(value: number | null | undefined) { if (!value) return "-"; return value < 60_000 ? `${Math.round(value / 1000)}s` : `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1000)}s`; }
