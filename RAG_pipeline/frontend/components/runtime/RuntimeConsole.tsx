"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  HelpCircle,
  RefreshCw,
  Server,
  TimerOff,
  X,
} from "lucide-react";
import {
  useRuntimeEngines,
  useRuntimeExecution,
  useRuntimeExecutions,
  useRuntimeIncident,
  useRuntimeIncidents,
  useRuntimeJobReliability,
  useRuntimeJobs,
  useRuntimeOverview,
  useRuntimePlans,
  useRuntimeSources,
  useRuntimeTrend,
} from "@/hooks/useRuntimeLogs";
import { useUserProjects } from "@/hooks/useUserProjects";
import { useChatContext } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";
import type {
  RuntimeEngine,
  RuntimeExecution,
  RuntimeExecutionState,
  RuntimeIncidentInsight,
  RuntimeJob,
  RuntimeJobReliability,
  RuntimePlan,
  RuntimeSource,
} from "@/types";
import { RuntimeInsightPanel } from "./RuntimeInsightPanel";
import { RuntimeLogViewer } from "./RuntimeLogViewer";
import { RuntimeOutcomeTrend } from "./RuntimeOutcomeTrend";
import {
  RuntimeHotspotsPanel,
  RuntimeReliabilityPanel,
  RuntimeSignaturesPanel,
  RuntimeThroughputPanel,
} from "./RuntimeAggregatePanels";
import {
  CauseOriginBadge,
  ConfidenceBadge,
  Empty,
  EntityTable,
  Kpi,
  PAGE_SIZE,
  PaginatedTable,
  Panel,
  PatternIdTag,
  QueryState,
  formatAge,
  formatCompact,
  formatDate,
  formatDuration,
  formatInterval,
  formatPercent,
} from "./runtime-ui";
import {
  DEFAULT_WINDOW_KEY,
  RUNTIME_WINDOWS,
  resolveWindow,
  windowSince,
  type RuntimeWindowKey,
} from "./runtime-window";

export type RuntimeConsoleView =
  | "hub"
  | "executions"
  | "jobs"
  | "plans"
  | "incidents"
  | "performance"
  | "signatures"
  | "estate";

const NAV: Array<{ view: RuntimeConsoleView; href: string; label: string }> = [
  { view: "hub", href: "/admin/logs", label: "Overview" },
  { view: "executions", href: "/admin/logs/executions", label: "Runs" },
  { view: "jobs", href: "/admin/logs/jobs", label: "Jobs" },
  { view: "plans", href: "/admin/logs/plans", label: "Plans" },
  { view: "incidents", href: "/admin/logs/incidents", label: "Incidents" },
  { view: "performance", href: "/admin/logs/performance", label: "Performance" },
  { view: "signatures", href: "/admin/logs/signatures", label: "Signatures" },
  { view: "estate", href: "/admin/logs/estate", label: "Estate" },
];

type Detail =
  | { kind: "execution"; item: RuntimeExecution }
  | { kind: "incident"; item: RuntimeIncidentInsight }
  | { kind: "job"; item: RuntimeJob }
  | { kind: "plan"; item: RuntimePlan }
  | { kind: "source"; item: RuntimeSource }
  | { kind: "engine"; item: RuntimeEngine };

interface ViewProps {
  scope?: string;
  since: string;
  windowKey: RuntimeWindowKey;
  onDetail: (detail: Detail) => void;
}

export function RuntimeConsole({ view }: { view: RuntimeConsoleView }) {
  const [projectId, setProjectId] = useState("");
  const [windowKey, setWindowKey] = useState<RuntimeWindowKey>(DEFAULT_WINDOW_KEY);
  const [detail, setDetail] = useState<Detail | null>(null);
  const projects = useUserProjects();
  const scope = projectId || undefined;

  // Recomputed only when the window changes, so a re-render does not shift the
  // lower bound and refetch every aggregate.
  const since = useMemo(() => windowSince(windowKey), [windowKey]);
  const viewProps: ViewProps = { scope, since, windowKey, onDetail: setDetail };

  return (
    <section className="mx-auto flex w-full max-w-7xl flex-col gap-6 overflow-x-hidden pb-8">
      <header className="flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-sm font-medium text-muted-foreground">Runtime operations</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-normal text-foreground">
            Operational console
          </h1>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row">
          <label className="flex min-w-0 flex-col gap-1 text-sm text-muted-foreground sm:w-64">
            Project scope
            <select
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
            >
              <option value="">All permitted projects</option>
              {projects.data?.projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex min-w-0 flex-col gap-1 text-sm text-muted-foreground sm:w-44">
            Time window
            <select
              value={windowKey}
              onChange={(event) => setWindowKey(event.target.value as RuntimeWindowKey)}
              className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
            >
              {RUNTIME_WINDOWS.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>
      <nav
        aria-label="Runtime views"
        className="-mx-1 flex gap-1 overflow-x-auto border-b border-border px-1"
      >
        {NAV.map((item) => (
          <Link
            key={item.view}
            href={item.href}
            className={cn(
              "shrink-0 border-b-2 px-3 py-2 text-sm font-medium transition-colors motion-reduce:transition-none",
              item.view === view
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>
      {view === "hub" && <Hub {...viewProps} />}
      {view === "executions" && <Executions {...viewProps} />}
      {view === "jobs" && <Jobs {...viewProps} />}
      {view === "plans" && <Plans {...viewProps} />}
      {view === "incidents" && <Incidents {...viewProps} />}
      {view === "performance" && <Performance {...viewProps} />}
      {view === "signatures" && <RuntimeSignaturesPanel scope={scope} since={since} />}
      {view === "estate" && <Estate {...viewProps} />}
      <DetailDrawer detail={detail} onClose={() => setDetail(null)} />
    </section>
  );
}

function Hub({ scope, since, windowKey, onDetail }: ViewProps) {
  const bucket = resolveWindow(windowKey).bucket;
  const overview = useRuntimeOverview({ projectId: scope });
  const trend = useRuntimeTrend({ projectId: scope, since, bucket });
  // Window-scoped, so a job that last ran before the window cannot appear here.
  const reliability = useRuntimeJobReliability({ projectId: scope, since, limit: 100 });
  const runs = useRuntimeExecutions({ projectId: scope, since, limit: 12 });
  const incidents = useRuntimeIncidents({ projectId: scope, since, limit: 1 });

  const estate = overview.data?.overview;
  const points = trend.data?.points ?? [];
  const jobs = reliability.data?.jobs ?? [];

  const totals = points.reduce(
    (acc, point) => ({
      runs: acc.runs + point.total_runs,
      failed: acc.failed + point.failed_runs,
    }),
    { runs: 0, failed: 0 },
  );
  const failureRate = totals.runs > 0 ? totals.failed / totals.runs : 0;
  const unfinished = jobs.reduce((sum, job) => sum + job.unfinished_runs, 0);

  const attention = jobs
    .filter((job) => job.failed_runs > 0 || job.unfinished_runs > 0)
    .sort(
      (a, b) =>
        b.failed_runs - a.failed_runs ||
        b.unfinished_runs - a.unfinished_runs ||
        b.failure_rate - a.failure_rate,
    )
    .slice(0, 5);

  return (
    <>
      <AskLogs scope={scope} />
      <SyncStatusBanner scope={scope} />
      <QueryState queries={[overview, trend, reliability, runs, incidents]}>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Kpi
            icon={<Activity />}
            label="Runs"
            value={formatCompact(totals.runs)}
            hint={resolveWindow(windowKey).label.toLowerCase()}
          />
          <Kpi
            icon={<CircleAlert />}
            label="Failure rate"
            value={formatPercent(failureRate)}
            hint={`${formatCompact(totals.failed)} failed`}
            tone={totals.failed > 0 ? "danger" : "good"}
          />
          <Kpi
            icon={<TimerOff />}
            label="Never finished"
            value={formatCompact(unfinished)}
            hint="runs with no end time"
            tone={unfinished > 0 ? "warning" : "good"}
          />
          <Kpi
            icon={<Server />}
            label="Engines online"
            value={`${estate?.engines_online ?? 0}/${estate?.engines_total ?? 0}`}
            tone={
              (estate?.engines_total ?? 0) > 0 &&
              estate?.engines_online === estate?.engines_total
                ? "good"
                : "warning"
            }
          />
        </div>
        <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
          <Panel
            title="Outcome trend"
            action={
              <Link
                href="/admin/logs/executions"
                className="text-sm text-primary hover:underline"
              >
                View runs
              </Link>
            }
          >
            <RuntimeOutcomeTrend points={points} bucket={trend.data?.bucket ?? bucket} />
          </Panel>
          <Panel
            title="Needs attention"
            description="Jobs that ran in this window and are failing or never finishing."
            action={
              <Link
                href="/admin/logs/incidents"
                className="text-sm text-primary hover:underline"
              >
                {incidents.data?.total ?? 0} incidents
              </Link>
            }
          >
            {attention.length === 0 ? (
              <Empty label="No active attention signals." />
            ) : (
              <ul className="divide-y divide-border">
                {attention.map((job) => (
                  <li key={`${job.source_key}:${job.job_name}`} className="py-3">
                    <AttentionRow job={job} />
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
        <Panel
          title="Recent runs"
          action={
            <Link href="/admin/logs/executions" className="text-sm text-primary hover:underline">
              All runs
            </Link>
          }
        >
          <RunTable rows={runs.data?.executions ?? []} onDetail={onDetail} compact />
        </Panel>
      </QueryState>
    </>
  );
}

function AttentionRow({ job }: { job: RuntimeJobReliability }) {
  const parts = [
    `${job.failed_runs} of ${job.total_runs} failed (${formatPercent(job.failure_rate)})`,
  ];
  if (job.unfinished_runs > 0) parts.push(`${job.unfinished_runs} never finished`);
  if (job.current_failure_streak > 1) parts.push(`${job.current_failure_streak} in a row`);

  return (
    <div className="flex items-start gap-3">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-foreground">{job.job_name}</p>
        <p className="truncate text-xs text-muted-foreground">{parts.join(" · ")}</p>
        <p className="text-xs text-muted-foreground">Last run {formatDate(job.last_run_at)}</p>
      </div>
    </div>
  );
}

const STATE_OPTIONS: Array<{ value: "" | RuntimeExecutionState; label: string }> = [
  { value: "", label: "All outcomes" },
  { value: "failed", label: "Failed" },
  { value: "success", label: "Succeeded" },
  { value: "running", label: "Running" },
  { value: "unknown", label: "Unknown" },
];

const DURATION_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 0, label: "Any duration" },
  { value: 60_000, label: "Longer than 1m" },
  { value: 600_000, label: "Longer than 10m" },
  { value: 3_600_000, label: "Longer than 1h" },
  { value: 86_400_000, label: "Longer than 24h" },
];

function Executions({ scope, since, onDetail }: ViewProps) {
  const [page, setPage] = useState(0);
  const [state, setState] = useState<"" | RuntimeExecutionState>("");
  const [minDurationMs, setMinDurationMs] = useState(0);
  const [unfinished, setUnfinished] = useState(false);

  useEffect(() => setPage(0), [scope, since, state, minDurationMs, unfinished]);

  const query = useRuntimeExecutions({
    projectId: scope,
    since,
    state: state || undefined,
    minDurationMs: minDurationMs || undefined,
    unfinished: unfinished || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  return (
    <QueryState queries={[query]}>
      <Panel
        title="Runs"
        description="One row per execution. Job-run mirrors are intentionally not shown."
        action={
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <select
              value={state}
              onChange={(event) => setState(event.target.value as "" | RuntimeExecutionState)}
              aria-label="Outcome"
              className="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground"
            >
              {STATE_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              value={minDurationMs}
              onChange={(event) => setMinDurationMs(Number(event.target.value))}
              aria-label="Minimum duration"
              className="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground"
            >
              {DURATION_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={unfinished}
                onChange={(event) => setUnfinished(event.target.checked)}
                className="h-3.5 w-3.5 rounded border-input"
              />
              Never finished only
            </label>
          </div>
        }
      >
        <PaginatedTable
          rows={query.data?.executions ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => <RunTable rows={rows} onDetail={onDetail} />}
        />
      </Panel>
    </QueryState>
  );
}

function Jobs({ scope, since, onDetail }: ViewProps) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [scope]);
  const query = useRuntimeJobs({ projectId: scope, limit: PAGE_SIZE, offset: page * PAGE_SIZE });

  return (
    <div className="flex flex-col gap-6">
      <RuntimeReliabilityPanel scope={scope} since={since} />
      <QueryState queries={[query]}>
        <Panel title="Job inventory">
          <PaginatedTable
            rows={query.data?.jobs ?? []}
            total={query.data?.total ?? 0}
            page={page}
            onPage={setPage}
            render={(rows) => (
              <EntityTable
                headers={["Job", "Environment", "Engine", "Status"]}
                rows={rows}
                rowKey={(job) => job.job_id}
                onClick={(job) => onDetail({ kind: "job", item: job })}
                cells={(job) => [
                  job.display_name ?? job.name,
                  job.environment_name ?? "-",
                  job.engine_name ?? "-",
                  job.status ?? "unknown",
                ]}
              />
            )}
          />
        </Panel>
      </QueryState>
    </div>
  );
}

function Plans({ scope, onDetail }: ViewProps) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [scope]);
  const query = useRuntimePlans({ projectId: scope, limit: PAGE_SIZE, offset: page * PAGE_SIZE });

  return (
    <QueryState queries={[query]}>
      <Panel title="Plans">
        <PaginatedTable
          rows={query.data?.plans ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => (
            <EntityTable
              headers={["Plan", "Environment", "Steps", "Status"]}
              rows={rows}
              rowKey={(plan) => plan.plan_id}
              onClick={(plan) => onDetail({ kind: "plan", item: plan })}
              cells={(plan) => [
                plan.display_name ?? plan.name,
                plan.environment_name ?? "-",
                String(plan.step_count ?? plan.steps.length),
                plan.status ?? "unknown",
              ]}
            />
          )}
        />
      </Panel>
    </QueryState>
  );
}

function Incidents({ scope, since, onDetail }: ViewProps) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [scope, since]);
  const query = useRuntimeIncidents({
    projectId: scope,
    since,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  return (
    <QueryState queries={[query]}>
      <Panel
        title="Incidents"
        description="A likely cause is an inference, not a confirmed diagnosis. Confidence is shown for every row."
      >
        <PaginatedTable
          rows={query.data?.incidents ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => (
            <EntityTable
              headers={["Job", "Likely cause", "Basis", "First bad"]}
              rows={rows}
              rowKey={(incident) => incident.incident_id}
              onClick={(incident) => onDetail({ kind: "incident", item: incident })}
              cells={(incident) => [
                incident.job_name ?? incident.execution_id,
                // The log's own sentence beats a template when it is available.
                incident.causal_message ||
                  incident.root_cause_candidate ||
                  incident.symptom_summary ||
                  "-",
                // Confidence and provenance travel together: what the claim is
                // worth, and who made it.
                <span key="basis" className="flex flex-col gap-0.5">
                  <ConfidenceBadge confidence={incident.confidence} />
                  <CauseOriginBadge origin={incident.cause_origin} />
                </span>,
                formatDate(incident.first_bad_at),
              ]}
            />
          )}
        />
      </Panel>
    </QueryState>
  );
}

function Performance({ scope, since }: ViewProps) {
  return (
    <div className="flex flex-col gap-6">
      <RuntimeHotspotsPanel scope={scope} since={since} />
      <RuntimeThroughputPanel scope={scope} since={since} />
    </div>
  );
}

function Estate({ scope, onDetail }: ViewProps) {
  const sources = useRuntimeSources({ projectId: scope, limit: 100 });
  const engines = useRuntimeEngines({ projectId: scope, limit: 100 });

  return (
    <QueryState queries={[sources, engines]}>
      <div className="grid gap-6 xl:grid-cols-2">
        <Panel title="Runtime sources">
          <EntityTable
            headers={["Source", "Type", "Status", "Last sync"]}
            rows={sources.data?.sources ?? []}
            rowKey={(source) => `${source.project_id}:${source.source_key}`}
            onClick={(source) => onDetail({ kind: "source", item: source })}
            cells={(source) => [
              source.label,
              source.source_type,
              source.status,
              formatDate(source.last_synced_at),
            ]}
          />
        </Panel>
        <Panel title="Engines">
          <EntityTable
            headers={["Engine", "Environment", "Version", "Status"]}
            rows={engines.data?.engines ?? []}
            rowKey={(engine) => engine.engine_id}
            onClick={(engine) => onDetail({ kind: "engine", item: engine })}
            cells={(engine) => [
              engine.name,
              engine.environment_name ?? "-",
              engine.version ?? "-",
              engine.status,
            ]}
          />
        </Panel>
      </div>
    </QueryState>
  );
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

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-3 border border-border bg-muted/35 p-4 sm:flex-row sm:items-end"
    >
      <label className="min-w-0 flex-1 text-sm font-medium">
        Ask these logs
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="What changed in the failed runs?"
          className="mt-1 h-10 w-full rounded-md border border-input bg-background px-3 text-sm font-normal"
        />
      </label>
      <button
        type="submit"
        disabled={!question.trim() || sending}
        className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        <ArrowRight className="h-4 w-4" />
        {sending ? "Opening chat" : "Ask Nexus"}
      </button>
    </form>
  );
}

function DetailDrawer({ detail, onClose }: { detail: Detail | null; onClose: () => void }) {
  const execution = useRuntimeExecution(
    detail?.kind === "execution" ? detail.item.execution_id : null,
  );
  const incident = useRuntimeIncident(
    detail?.kind === "incident" ? detail.item.incident_id : null,
  );
  if (!detail) return null;

  const item =
    detail.kind === "execution"
      ? execution.data?.execution ?? detail.item
      : detail.kind === "incident"
        ? incident.data?.incident ?? detail.item
        : detail.item;

  // Both kinds resolve to an execution, which is what the insight panel and the
  // log viewer key on.
  const executionId =
    detail.kind === "execution"
      ? detail.item.execution_id
      : detail.kind === "incident"
        ? detail.item.execution_id
        : null;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-foreground/20 p-0 sm:p-4"
      role="presentation"
      onMouseDown={onClose}
    >
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Runtime details"
        onMouseDown={(event) => event.stopPropagation()}
        className="flex h-full w-full max-w-2xl flex-col border-l border-border bg-background shadow-xl sm:border sm:shadow-2xl"
      >
        <div className="flex items-start justify-between border-b border-border p-5">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase text-muted-foreground">{detail.kind}</p>
            <h2 className="mt-1 truncate text-lg font-semibold">
              {detailTitle(detail, item as Record<string, unknown>)}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Close details"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="flex min-w-0 flex-col gap-6">
            <DetailFields item={item as Record<string, unknown>} />
            {executionId && <RuntimeInsightPanel executionId={executionId} />}
            {executionId && <RuntimeLogViewer executionId={executionId} />}
          </div>
        </div>
      </aside>
    </div>
  );
}

function DetailFields({ item }: { item: Record<string, unknown> }) {
  const entries = Object.entries(item).filter(
    ([, value]) => value !== null && value !== undefined && value !== "",
  );
  const scalars = entries.filter(([, value]) => typeof value !== "object");
  // Arrays and nested objects were previously dropped, which hid plan steps,
  // step results, next checks and evidence refs entirely.
  const structured = entries.filter(
    ([, value]) => typeof value === "object" && !isEmptyStructure(value),
  );

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <dl className="grid gap-4 sm:grid-cols-2">
        {scalars.map(([key, value]) => (
          <div key={key} className="border-b border-border pb-3">
            <dt className="text-xs font-medium uppercase text-muted-foreground">
              {humanize(key)}
            </dt>
            <dd className="mt-1 break-words text-sm text-foreground">{String(value)}</dd>
          </div>
        ))}
      </dl>
      {structured.map(([key, value]) => (
        <section key={key} className="min-w-0">
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {humanize(key)}
          </h3>
          <div className="mt-2 min-w-0">
            <StructuredValue value={value} />
          </div>
        </section>
      ))}
    </div>
  );
}

function StructuredValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <Empty label="None recorded." />;
    const allPrimitive = value.every((entry) => entry === null || typeof entry !== "object");
    if (allPrimitive) {
      return (
        <ol className="list-decimal space-y-1 pl-5 text-sm text-foreground">
          {value.map((entry, index) => (
            <li key={index} className="break-words">
              {String(entry)}
            </li>
          ))}
        </ol>
      );
    }
    const rows = value.filter(
      (entry): entry is Record<string, unknown> => typeof entry === "object" && entry !== null,
    );
    const headers = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
    return (
      <EntityTable
        headers={headers.map(humanize)}
        rows={rows}
        rowKey={(row) => JSON.stringify(row).slice(0, 120)}
        cells={(row) =>
          headers.map((header) => {
            const cell = row[header];
            return cell === null || cell === undefined || cell === ""
              ? "-"
              : typeof cell === "object"
                ? JSON.stringify(cell)
                : String(cell);
          })
        }
      />
    );
  }

  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(
      ([, entry]) => entry !== null && entry !== undefined && entry !== "",
    );
    if (entries.length === 0) return <Empty label="None recorded." />;
    return (
      <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
        {entries.map(([key, entry]) => (
          <div key={key} className="min-w-0">
            <span className="text-muted-foreground">{humanize(key)}: </span>
            <span className="break-words text-foreground">
              {typeof entry === "object" ? JSON.stringify(entry) : String(entry)}
            </span>
          </div>
        ))}
      </dl>
    );
  }

  return <span className="text-sm text-foreground">{String(value)}</span>;
}

function isEmptyStructure(value: unknown): boolean {
  if (Array.isArray(value)) return value.length === 0;
  if (value && typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

function humanize(key: string): string {
  return key.replaceAll("_", " ");
}

function RunTable({
  rows,
  onDetail,
  compact = false,
}: {
  rows: RuntimeExecution[];
  onDetail: (detail: Detail) => void;
  compact?: boolean;
}) {
  return (
    <EntityTable
      headers={
        compact
          ? ["Run", "Outcome", "Started"]
          : ["Run", "Job", "Outcome", "Started", "Duration"]
      }
      rows={rows}
      rowKey={(run) => run.execution_id}
      onClick={(run) => onDetail({ kind: "execution", item: run })}
      cells={(run) =>
        compact
          ? [run.job_name, <StateCell key="state" run={run} />, formatDate(run.started_at)]
          : [
              run.execution_id,
              run.job_name,
              <StateCell key="state" run={run} />,
              formatDate(run.started_at),
              run.ended_at ? formatDuration(run.duration_ms) : "never finished",
            ]
      }
    />
  );
}

/** Outcome is state: icon + label, so it never rests on colour alone. */
function StateCell({ run }: { run: RuntimeExecution }) {
  const unfinished = !run.ended_at && run.state !== "running";
  if (run.state === "failed") {
    return (
      <span className="inline-flex items-center gap-1 text-destructive">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
        failed
      </span>
    );
  }
  if (unfinished) {
    return (
      <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
        <TimerOff className="h-3.5 w-3.5 shrink-0" />
        {run.state} · no end
      </span>
    );
  }
  if (run.state === "success") {
    return (
      <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
        success
      </span>
    );
  }
  return <span className="text-muted-foreground">{run.state}</span>;
}

function detailTitle(detail: Detail, item: Record<string, unknown>) {
  return String(
    item.execution_id ??
      item.incident_id ??
      item.job_id ??
      item.plan_id ??
      item.engine_id ??
      item.label ??
      detail.kind,
  );
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
    message =
      "No runtime sources are enabled for polling. Enable a source to start importing data.";
  } else if (ageMs === null) {
    message = `No runtime data imported yet. ${polling.length} source(s) set to poll every ${formatInterval(minInterval)}.`;
  } else if (isStale) {
    message = `Last import ${formatAge(ageMs)} ago — data may be stale. ${polling.length} source(s) set to poll every ${formatInterval(minInterval)}.`;
  } else {
    message = `Last import ${formatAge(ageMs)} ago · ${polling.length} source(s) polling every ${formatInterval(minInterval)}.`;
  }

  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-lg border px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between",
        isStale
          ? "border-amber-500/40 bg-amber-500/5 text-amber-700 dark:text-amber-400"
          : "border-border bg-muted/30 text-muted-foreground",
      )}
    >
      <span className="flex items-center gap-2">
        {isStale ? (
          <AlertTriangle className="h-4 w-4 shrink-0" />
        ) : (
          <RefreshCw className="h-4 w-4 shrink-0" />
        )}
        <span>{message}</span>
      </span>
      <Link
        href="/admin/runtime"
        className="shrink-0 font-medium text-primary hover:underline"
      >
        Manage sources
      </Link>
    </div>
  );
}
