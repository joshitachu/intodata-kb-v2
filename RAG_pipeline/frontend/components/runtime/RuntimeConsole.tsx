"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CalendarX,
  CheckCircle2,
  CircleAlert,
  Clock,
  FolderOpen,
  Gauge,
  LayoutGrid,
  ListTree,
  Network,
  Pause,
  Play,
  RefreshCw,
  ScrollText,
  Server,
  Sparkles,
  TimerOff,
  Workflow,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SelectMenu } from "@/components/ui/select-menu";
import {
  useRuntimeEngines,
  useRuntimeDeployments,
  useRuntimeExecution,
  useRuntimeExecutions,
  useRuntimeIncident,
  useRuntimeIncidents,
  useRuntimeJobInsights,
  useRuntimeJobReliability,
  useRuntimeJobRuns,
  useRuntimeJobs,
  useRuntimeOverview,
  useRuntimePlanRuns,
  useRuntimePlans,
  useRuntimeSources,
  useRuntimeTrend,
} from "@/hooks/useRuntimeLogs";
import { useUserProjects } from "@/hooks/useUserProjects";
import { useChatContext } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";
import type {
  RuntimeEngine,
  RuntimeDeployment,
  RuntimeExecution,
  RuntimeExecutionState,
  RuntimeIncidentInsight,
  RuntimeJob,
  RuntimeJobInsight,
  RuntimeJobReliability,
  RuntimeJobRun,
  RuntimePlan,
  RuntimePlanRun,
  RuntimeSource,
} from "@/types";
import { RuntimeInsightPanel } from "./RuntimeInsightPanel";
import { RuntimeLogViewer } from "./RuntimeLogViewer";
import { RuntimeOutcomeTrend } from "./RuntimeOutcomeTrend";
import {
  ExecutionTypeBadge,
  RuntimeOutcomeBadge,
  RuntimeOutcomeFilter,
} from "./RuntimeOutcomeFilter";
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
  LiveDot,
  PAGE_SIZE,
  PaginatedTable,
  Panel,
  QueryState,
  RelativeTime,
  formatAge,
  formatCompact,
  formatDuration,
  formatInterval,
  formatPercent,
  useNow,
} from "./runtime-ui";
import {
  DEFAULT_WINDOW_KEY,
  RUNTIME_WINDOWS,
  isRuntimeWindowKey,
  resolveWindow,
  windowSince,
  type RuntimeWindowKey,
} from "./runtime-window";

export type RuntimeConsoleView =
  | "hub"
  | "executions"
  | "jobs"
  | "routes"
  | "data_services"
  | "plans"
  | "incidents"
  | "performance"
  | "signatures"
  | "estate";

const NAV: Array<{
  view: RuntimeConsoleView;
  href: string;
  label: string;
  icon: typeof Activity;
}> = [
  { view: "hub", href: "/admin/logs", label: "Overview", icon: LayoutGrid },
  { view: "executions", href: "/admin/logs/executions", label: "Runs", icon: ScrollText },
  { view: "jobs", href: "/admin/logs/jobs", label: "Jobs", icon: Workflow },
  { view: "routes", href: "/admin/logs/routes", label: "Routes", icon: Activity },
  { view: "data_services", href: "/admin/logs/data-services", label: "Data services", icon: Server },
  { view: "plans", href: "/admin/logs/plans", label: "Plans", icon: ListTree },
  { view: "incidents", href: "/admin/logs/incidents", label: "Incidents", icon: AlertTriangle },
  { view: "performance", href: "/admin/logs/performance", label: "Performance", icon: Gauge },
  { view: "signatures", href: "/admin/logs/signatures", label: "Signatures", icon: Sparkles },
  { view: "estate", href: "/admin/logs/estate", label: "Estate", icon: Network },
];

/** Automatic refresh cadence for the console when live mode is on. */
const LIVE_REFRESH_MS = 30_000;

interface SyncFreshness {
  hasSources: boolean;
  isStale: boolean;
  ageMs: number | null;
  pollingCount: number;
  minInterval: number;
  message: string;
}

/**
 * How fresh the imported runtime data is.
 *
 * The console shows live-updating numbers, so it must be explicit about the one
 * thing a reader would otherwise assume: whether anything is still arriving. A
 * pulsing dot over a three-hour-old import would be a lie, so the header pill
 * and the hub banner both derive from this single computation.
 */
function useSyncFreshness(scope?: string): SyncFreshness | null {
  const sources = useRuntimeSources({ projectId: scope, limit: 100 });
  // Recomputed on the same tick as the relative timestamps, so "2m ago" in the
  // header does not freeze between refetches.
  const now = useNow();
  const rows = sources.data?.sources ?? [];

  if (sources.isLoading || rows.length === 0) return null;

  const polling = rows.filter((source) => source.enabled !== false);
  const syncedTimes = rows
    .map((source) => source.last_synced_at)
    .filter((value): value is string => Boolean(value))
    .map((value) => new Date(value).getTime());
  const freshest = syncedTimes.length ? Math.max(...syncedTimes) : null;
  const ageMs = freshest === null ? null : now - freshest;
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

  return { hasSources: true, isStale, ageMs, pollingCount: polling.length, minInterval, message };
}

type Detail =
  | { kind: "execution"; item: RuntimeExecution }
  | { kind: "job_run"; item: RuntimeJobRun }
  | { kind: "plan_run"; item: RuntimePlanRun }
  | { kind: "deployment"; item: RuntimeDeployment }
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
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const requestedProjectId = searchParams.get("project")?.trim() ?? "";
  const requestedWindowKey = searchParams.get("window");
  const [projectId, setProjectId] = useState(requestedProjectId);
  const [windowKey, setWindowKey] = useState<RuntimeWindowKey>(
    isRuntimeWindowKey(requestedWindowKey) ? requestedWindowKey : DEFAULT_WINDOW_KEY,
  );
  const [detail, setDetail] = useState<Detail | null>(null);
  const [live, setLive] = useState(true);
  const projects = useUserProjects();
  const sourceCatalog = useRuntimeSources({ limit: 100 });
  const queryClient = useQueryClient();

  // A runtime source has no owning project: `linked_project_ids` is every
  // project attached to it, and any of them can query this console. Reading a
  // single project off the source (the old `project_id`) hid every project that
  // reached the tenant through an attachment.
  const tmcProjectIds = useMemo(
    () => new Set(
      (sourceCatalog.data?.sources ?? [])
        .filter((source) => ["tmc", "talend_tmc"].includes(source.source_type.toLowerCase()))
        .flatMap((source) => source.linked_project_ids),
    ),
    [sourceCatalog.data?.sources],
  );
  const permittedProjects = useMemo(
    () => (projects.data?.projects ?? []).filter((project) => tmcProjectIds.has(project.id)),
    [projects.data?.projects, tmcProjectIds],
  );
  const projectListLoading = projects.isLoading || sourceCatalog.isLoading;
  const hasSelectedProject = permittedProjects.some((project) => project.id === projectId);

  // Never issue an aggregate runtime query while the permitted-project list is
  // loading. The console is intentionally single-project: an absent project is
  // a transient selection state, not permission to query every project.
  const scope = hasSelectedProject ? projectId : "__no_runtime_project_selected__";
  const freshness = useSyncFreshness(scope);

  useEffect(() => {
    if (projectListLoading) return;
    const permittedIds = new Set(permittedProjects.map((project) => project.id));
    const nextProjectId = permittedIds.has(requestedProjectId)
      ? requestedProjectId
      : permittedIds.has(projectId)
        ? projectId
        : permittedProjects[0]?.id ?? "";

    if (nextProjectId !== projectId) setProjectId(nextProjectId);
    if (nextProjectId && nextProjectId !== requestedProjectId) {
      const params = new URLSearchParams(searchParams.toString());
      params.set("project", nextProjectId);
      params.set("window", windowKey);
      router.replace(`${pathname}?${params.toString()}`);
    }
  }, [pathname, permittedProjects, projectId, projectListLoading, requestedProjectId, router, searchParams, windowKey]);

  const selectProject = useCallback((nextProjectId: string) => {
    if (!permittedProjects.some((project) => project.id === nextProjectId)) return;
    setProjectId(nextProjectId);
    const params = new URLSearchParams(searchParams.toString());
    params.set("project", nextProjectId);
    params.set("window", windowKey);
    router.replace(`${pathname}?${params.toString()}`);
  }, [pathname, permittedProjects, router, searchParams, windowKey]);

  const selectWindow = useCallback((nextWindowKey: string) => {
    if (!isRuntimeWindowKey(nextWindowKey)) return;
    setWindowKey(nextWindowKey);
    const params = new URLSearchParams(searchParams.toString());
    params.set("window", nextWindowKey);
    if (projectId) params.set("project", projectId);
    router.replace(`${pathname}?${params.toString()}`);
  }, [pathname, projectId, router, searchParams]);

  const runtimeHref = useCallback((href: string) => {
    const params = new URLSearchParams();
    if (projectId) params.set("project", projectId);
    params.set("window", windowKey);
    return `${href}?${params.toString()}`;
  }, [projectId, windowKey]);

  // Recomputed only when the window changes, so a re-render does not shift the
  // lower bound and refetch every aggregate.
  const since = useMemo(() => windowSince(windowKey), [windowKey]);
  const viewProps: ViewProps = { scope, since, windowKey, onDetail: setDetail };

  // Invalidating the whole `runtime` key refetches only what is MOUNTED, so the
  // active view stays current without paying for the seven views that are not.
  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["runtime"] });
  }, [queryClient]);

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(refresh, LIVE_REFRESH_MS);
    return () => clearInterval(timer);
  }, [live, refresh]);

  const projectOptions = permittedProjects.map((project) => ({
    value: project.id,
    label: project.display_name,
  }));

  return (
    <section className="mx-auto flex w-full max-w-7xl flex-col gap-5 overflow-x-hidden pb-10">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0" data-tour="runtime-header">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">Runtime logs</h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
            <span>What ran, what failed, and why.</span>
            {freshness && (
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
                  freshness.isStale
                    ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
                    : "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
                )}
              >
                <LiveDot live={!freshness.isStale && live} />
                {freshness.isStale
                  ? freshness.ageMs === null
                    ? "No data imported"
                    : `Stale · ${formatAge(freshness.ageMs)} old`
                  : `Live · imported ${freshness.ageMs === null ? "-" : formatAge(freshness.ageMs)} ago`}
              </span>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SelectMenu
            value={projectId}
            onChange={selectProject}
            options={projectOptions}
            placeholder={projectListLoading ? "Loading TMC projects…" : "No TMC projects"}
            icon={FolderOpen}
            ariaLabel="Project scope"
            variant="field"
            size="md"
            align="end"
          />
          <SelectMenu
            value={windowKey}
            onChange={selectWindow}
            options={RUNTIME_WINDOWS.map((option) => ({ value: option.key, label: option.label }))}
            icon={Clock}
            ariaLabel="Time window"
            variant="field"
            size="md"
            align="end"
            className="max-w-[11rem]"
          />
          <Button
            variant="secondary"
            size="md"
            onClick={() => setLive((current) => !current)}
            aria-pressed={live}
            title={
              live
                ? `Auto-refreshing every ${formatInterval(LIVE_REFRESH_MS / 1000)}`
                : "Auto-refresh is paused"
            }
          >
            {live ? <Pause className="h-3.5 w-3.5" aria-hidden /> : <Play className="h-3.5 w-3.5" aria-hidden />}
            {live ? "Pause" : "Resume"}
          </Button>
          <Button variant="secondary" size="md" onClick={refresh} aria-label="Refresh now">
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Refresh
          </Button>
        </div>
      </header>
      <nav
        data-tour="admin-logs-views"
        aria-label="Runtime views"
        className="flex gap-1 overflow-x-auto rounded-xl border border-border bg-muted/30 p-1"
      >
        {NAV.map((item) => {
          const Icon = item.icon;
          const isActive = item.view === view;
          return (
            <Link
              key={item.view}
              href={runtimeHref(item.href)}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "inline-flex shrink-0 items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors motion-reduce:transition-none",
                isActive
                  ? "bg-card text-foreground shadow-elevation-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {item.label}
            </Link>
          );
        })}
      </nav>
      {!hasSelectedProject && !projectListLoading && (
        <Empty label="No TMC projects" hint="Connect a Talend Management Console source to a permitted project." />
      )}
      {hasSelectedProject && view === "hub" && (
        <Hub {...viewProps} freshness={freshness} runtimeHref={runtimeHref} />
      )}
      {hasSelectedProject && view === "executions" && <Executions {...viewProps} />}
      {hasSelectedProject && view === "jobs" && <Jobs {...viewProps} />}
      {hasSelectedProject && view === "routes" && <Deployments {...viewProps} kind="route" />}
      {hasSelectedProject && view === "data_services" && <Deployments {...viewProps} kind="data_service" />}
      {hasSelectedProject && view === "plans" && <Plans {...viewProps} />}
      {hasSelectedProject && view === "incidents" && <Incidents {...viewProps} />}
      {hasSelectedProject && view === "performance" && <Performance {...viewProps} />}
      {hasSelectedProject && view === "signatures" && <RuntimeSignaturesPanel scope={scope} since={since} />}
      {hasSelectedProject && view === "estate" && <Estate {...viewProps} />}
      <DetailDrawer detail={detail} onClose={() => setDetail(null)} />
    </section>
  );
}

function Hub({
  scope,
  since,
  windowKey,
  onDetail,
  freshness,
  runtimeHref,
}: ViewProps & {
  freshness: SyncFreshness | null;
  runtimeHref: (href: string) => string;
}) {
  const bucket = resolveWindow(windowKey).bucket;
  const until = useMemo(
    () => new Date(new Date(since).getTime() + resolveWindow(windowKey).hours * 3_600_000).toISOString(),
    [since, windowKey],
  );
  const overview = useRuntimeOverview({ projectId: scope });
  const trend = useRuntimeTrend({ projectId: scope, since, bucket });
  // Window-scoped aggregates power the historical KPIs and evidence cards.
  const reliability = useRuntimeJobReliability({ projectId: scope, since, limit: 100 });
  const jobInsights = useRuntimeJobInsights({ projectId: scope, since, limit: 500 });
  // Attention deliberately has no selected-window filter: it answers whether
  // a job is unresolved now, not whether it had a problem sometime in history.
  const attentionReliability = useRuntimeJobReliability({
    projectId: scope,
    attentionOnly: true,
    limit: 100,
  });
  const attentionInsights = useRuntimeJobInsights({ projectId: scope, limit: 500 });
  const runs = useRuntimeExecutions({ projectId: scope, since, limit: 12 });

  const estate = overview.data?.overview;
  const points = trend.data?.points ?? [];
  const jobs = reliability.data?.jobs ?? [];
  const insights = jobInsights.data?.insights ?? [];
  const currentAttentionJobs = attentionReliability.data?.jobs ?? [];
  const attentionInsightsByJob = new Map(
    (attentionInsights.data?.insights ?? []).map((insight) => [
      `${insight.source_key}:${insight.job_name}`,
      insight,
    ]),
  );
  const totals = points.reduce(
    (acc, point) => ({
      runs: acc.runs + point.total_runs,
      failed: acc.failed + point.failed_runs,
      // A misfire never started; a timeout or termination stopped mid-flight.
      // None of the three were ever "unknown" -- a job that never ran is the
      // failure mode that used to hide completely, so it gets its own tile
      // below instead of vanishing into the failure rate.
      misfired: acc.misfired + point.misfired_runs,
      timedOut: acc.timedOut + point.timed_out_runs,
      terminated: acc.terminated + point.terminated_runs,
    }),
    { runs: 0, failed: 0, misfired: 0, timedOut: 0, terminated: 0 },
  );
  const failureRate = totals.runs > 0 ? totals.failed / totals.runs : 0;
  const unfinished = jobs.reduce((sum, job) => sum + job.unfinished_runs, 0);

  // The API is authoritative, but keep the display safe if an older backend
  // responds during a rolling deploy or a cached response is replayed.
  // TERMINATED is deliberately excluded here too, mirroring the backend's
  // _ATTENTION_STATES: a termination is an operator action, so somebody has
  // already looked (see RuntimeStoreRepository._needs_attention).
  const attention = currentAttentionJobs
    .filter(
      (job) =>
        job.latest_state === "failed" ||
        job.latest_state === "misfired" ||
        job.latest_state === "timed_out" ||
        job.latest_unfinished,
    )
    .slice(0, 5);

  const windowLabel = resolveWindow(windowKey).label.toLowerCase();

  return (
    <div className="flex flex-col gap-5">
      <SyncStatusBanner freshness={freshness} />
      <QueryState queries={[
        overview,
        trend,
        reliability,
        jobInsights,
        attentionReliability,
        attentionInsights,
        runs,
      ]}>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" data-tour="runtime-kpis">
          <Kpi
            icon={Activity}
            label="Runs"
            value={formatCompact(totals.runs)}
            hint={windowLabel}
          />
          <Kpi
            icon={CircleAlert}
            label="Failure rate"
            value={formatPercent(failureRate)}
            hint={`${formatCompact(totals.failed)} failed`}
            tone={totals.failed > 0 ? "danger" : "good"}
          />
          <Kpi
            icon={CalendarX}
            label="Misfired"
            value={formatCompact(totals.misfired)}
            hint={`${formatCompact(totals.timedOut)} timed out · ${formatCompact(totals.terminated)} terminated`}
            tone={totals.misfired > 0 ? "warning" : "good"}
          />
          <Kpi
            icon={TimerOff}
            label="Never finished"
            value={formatCompact(unfinished)}
            hint="runs with no end time"
            tone={unfinished > 0 ? "warning" : "good"}
          />
          <Kpi
            icon={Server}
            label="Engines online"
            value={`${estate?.engines_online ?? 0}/${estate?.engines_total ?? 0}`}
            hint={`${estate?.jobs_unhealthy ?? 0} unhealthy jobs`}
            tone={
              (estate?.engines_total ?? 0) > 0 &&
              estate?.engines_online === estate?.engines_total
                ? "good"
                : "warning"
            }
          />
        </div>
        <EvidenceQuality insights={insights} totalJobs={jobInsights.data?.total ?? insights.length} />
        <div className="grid gap-5 xl:grid-cols-[1.35fr_0.65fr]">
          <Panel
            title="Outcome trend"
            icon={Activity}
            description={`Run outcomes and duration across the ${windowLabel}.`}
            action={
              <Link
                href={runtimeHref("/admin/logs/executions")}
                className="text-xs font-medium text-primary hover:underline"
              >
                View runs
              </Link>
            }
          >
            <RuntimeOutcomeTrend
              points={points}
              bucket={trend.data?.bucket ?? bucket}
              coverage={trend.data?.coverage ?? {
                observed_from: null,
                observed_until: null,
                completeness: "unknown",
              }}
              rangeStart={since}
              rangeEnd={until}
            />
          </Panel>
          <Panel
            title="Needs attention"
            icon={AlertTriangle}
            description="Jobs with a latest observed run that is still unresolved."
            action={
              <Link
                href={runtimeHref("/admin/logs/jobs")}
                className="text-xs font-medium text-primary hover:underline"
              >
                {attentionReliability.data?.total ?? attention.length} active
              </Link>
            }
            flush
          >
            {attention.length === 0 ? (
              <Empty
                label="No active attention signals."
                hint="Each latest observed run is healthy or still in progress."
              />
            ) : (
              <ul className="divide-y divide-border">
                {attention.map((job) => (
                  <li key={`${job.source_key}:${job.job_name}`} className="px-5 py-3">
                    <AttentionRow
                      job={job}
                      insight={attentionInsightsByJob.get(`${job.source_key}:${job.job_name}`)}
                    />
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
        <Panel
          title="Recent runs"
          icon={ScrollText}
          description="Newest first. Select a row for logs, evidence and the derived cause."
          action={
            <Link
              href={runtimeHref("/admin/logs/executions")}
              className="text-xs font-medium text-primary hover:underline"
            >
              All runs
            </Link>
          }
          flush
        >
          <RunTable rows={runs.data?.executions ?? []} onDetail={onDetail} compact />
        </Panel>
        <AskLogs scope={scope} />
      </QueryState>
    </div>
  );
}

function EvidenceQuality({
  insights,
  totalJobs,
}: {
  insights: RuntimeJobInsight[];
  totalJobs: number;
}) {
  const totals = insights.reduce(
    (acc, insight) => ({
      failures: acc.failures + insight.failed_runs,
      withLogs: acc.withLogs + insight.failures_with_logs,
      diagnosed: acc.diagnosed + insight.diagnosed_failures,
      capped: acc.capped + insight.capture_ceiling_reached,
    }),
    { failures: 0, withLogs: 0, diagnosed: 0, capped: 0 },
  );

  if (totals.failures === 0) {
    const isPartial = insights.length < totalJobs;
    return (
      <Panel
        title="Evidence quality"
        icon={CheckCircle2}
        description="Whether failed runs have enough captured evidence to explain them."
      >
        <p className="text-sm text-muted-foreground">
          {isPartial
            ? "No failures appear in the retrieved job evidence."
            : "No failures in this window, so there is nothing to diagnose."}
        </p>
        {isPartial && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
            This covers {formatCompact(insights.length)} of {formatCompact(totalJobs)} jobs; the estate-wide result is incomplete.
          </p>
        )}
      </Panel>
    );
  }

  const unexplained = Math.max(0, totals.failures - totals.diagnosed);
  const missingLogs = Math.max(0, totals.failures - totals.withLogs);
  const isPartial = insights.length < totalJobs;
  const metrics = [
    {
      label: "Failure logs",
      value: `${formatCompact(totals.withLogs)}/${formatCompact(totals.failures)}`,
      hint: missingLogs === 0
        ? "captured for every failure"
        : `${formatCompact(missingLogs)} failure${missingLogs === 1 ? " has" : "s have"} no logs`,
    },
    {
      label: "Useful diagnoses",
      value: `${formatCompact(totals.diagnosed)}/${formatCompact(totals.failures)}`,
      hint: "supported by captured evidence",
    },
    {
      label: "Still unexplained",
      value: formatCompact(unexplained),
      hint: "not assigned a guessed cause",
    },
    {
      label: "Capture ceiling",
      value: formatCompact(totals.capped),
      hint: "logs that reached the line limit",
    },
  ];

  return (
    <Panel
      title="Evidence quality"
      icon={CheckCircle2}
      description="How much of the failure population the captured logs can honestly explain."
    >
      <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" data-tour="runtime-evidence">
        {metrics.map((metric) => (
          <div key={metric.label} className="min-w-0 rounded-lg border border-border bg-muted/20 p-3">
            <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {metric.label}
            </dt>
            <dd className="mt-1 text-lg font-semibold text-foreground">{metric.value}</dd>
            <p className="mt-0.5 text-xs text-muted-foreground">{metric.hint}</p>
          </div>
        ))}
      </dl>
      {isPartial && (
        <p className="mt-3 text-xs text-amber-700 dark:text-amber-400">
          Evidence totals cover {formatCompact(insights.length)} of {formatCompact(totalJobs)} jobs in this window.
        </p>
      )}
    </Panel>
  );
}

function patternLabel(patternId: string): string {
  return patternId
    .replaceAll("_", " ")
    .split(".")
    .map((part) => part.trim())
    .filter(Boolean)
    .join(" · ");
}

function AttentionRow({
  job,
  insight,
}: {
  job: RuntimeJobReliability;
  insight?: RuntimeJobInsight;
}) {
  const parts = [];
  if (job.latest_state === "failed") {
    parts.push("Latest run failed");
    if (job.current_failure_streak > 1) parts.push(`${job.current_failure_streak} failures in a row`);
  } else if (job.latest_state === "misfired") {
    // Never started, not a failure -- the schedule fired and the run never
    // began. This is the case that used to hide completely as "unknown".
    parts.push("Latest run never started");
  } else if (job.latest_state === "timed_out") {
    parts.push("Latest run timed out");
  }
  // TERMINATED is not a reason to appear here at all (see the `attention`
  // filter above) -- an operator stopped it deliberately.
  if (job.latest_unfinished) parts.push("Latest run never finished");

  return (
    <div className="flex items-start gap-2.5">
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-destructive/10 text-destructive">
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
      </span>
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-foreground">{job.job_name}</p>
        <p className="text-xs text-muted-foreground">{parts.join(" · ")}</p>
        {insight?.pattern_id ? (
          <div className="mt-1.5 space-y-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
              <span className="font-medium text-foreground">
                {patternLabel(insight.pattern_id)}
                {insight.occurrences > 0 ? ` · ${insight.occurrences} occurrence${insight.occurrences === 1 ? "" : "s"}` : ""}
              </span>
              <ConfidenceBadge confidence={insight.confidence} />
            </div>
            {(insight.failed_component || insight.termination_component) && (
              <p className="text-xs text-muted-foreground">
                {insight.failed_component ? `Origin: ${insight.failed_component}` : "Origin not identified"}
                {insight.termination_component
                  ? ` · Stopped by: ${insight.termination_component}`
                  : ""}
              </p>
            )}
            {insight.causal_message && (
              <p className="line-clamp-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground/80">Observed:</span>{" "}
                {insight.causal_message}
              </p>
            )}
            {insight.root_cause_candidate && (
              <p className="line-clamp-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground/80">Interpretation:</span>{" "}
                {insight.root_cause_candidate}
              </p>
            )}
          </div>
        ) : insight && job.latest_state === "failed" ? (
          <p className="mt-1 text-xs font-medium text-muted-foreground">
            No useful diagnosis yet.
          </p>
        ) : null}
        {insight && job.latest_state === "failed" && (
          <p className="mt-1 text-xs text-muted-foreground/80">
            {insight.failures_with_logs === 0
              ? `No logs captured for ${job.failed_runs} failure${job.failed_runs === 1 ? "" : "s"}`
              : `Logs ${insight.failures_with_logs}/${job.failed_runs}`}
            {` · Diagnosed ${insight.diagnosed_failures}/${job.failed_runs}`}
            {insight.capture_ceiling_reached > 0
              ? ` · ${insight.capture_ceiling_reached} reached the capture ceiling`
              : ""}
          </p>
        )}
        <p className="text-xs text-muted-foreground/80">
          Last run <RelativeTime value={job.last_run_at} />
        </p>
      </div>
    </div>
  );
}

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
        icon={ScrollText}
        description="One row per execution. Job-run mirrors are intentionally not shown."
        action={
          <div className="flex flex-wrap items-center gap-2">
            <RuntimeOutcomeFilter value={state} onChange={setState} />
            <SelectMenu
              value={String(minDurationMs)}
              onChange={(value) => setMinDurationMs(Number(value))}
              options={DURATION_OPTIONS.map((option) => ({
                value: String(option.value),
                label: option.label,
              }))}
              placeholder="Any duration"
              ariaLabel="Minimum duration"
              variant="field"
              align="end"
            />
            <label className="flex cursor-pointer items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted/40">
              <input
                type="checkbox"
                checked={unfinished}
                onChange={(event) => setUnfinished(event.target.checked)}
                className="h-3.5 w-3.5 rounded border-input accent-[hsl(var(--primary))]"
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
  const query = useRuntimeJobs({
    projectId: scope,
    executableKind: "job",
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  return (
    <div className="flex flex-col gap-6">
      <RuntimeReliabilityPanel scope={scope} since={since} />
      <JobRunsPanel scope={scope} since={since} onDetail={onDetail} />
      <QueryState queries={[query]}>
        <Panel
          title="Job inventory"
          icon={Workflow}
          description="Every job known to the runtime source, whether or not it ran in this window."
          flush
        >
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

/**
 * Per-run listing for jobs, filterable by outcome. `/runtime/job-runs` already
 * accepts `state` server-side (ListRuntimeJobRunsInput.state), so the filter
 * narrows this paginated query directly instead of hiding rows from an
 * already-fetched page -- which would silently lie about `total`.
 */
function JobRunsPanel({ scope, since, onDetail }: Pick<ViewProps, "scope" | "since" | "onDetail">) {
  const [page, setPage] = useState(0);
  const [state, setState] = useState<"" | RuntimeExecutionState>("");
  useEffect(() => setPage(0), [scope, since, state]);
  const query = useRuntimeJobRuns({
    projectId: scope,
    since,
    state: state || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  return (
    <QueryState queries={[query]}>
      <Panel
        title="Job runs"
        icon={ScrollText}
        description="Every observed run for jobs in this scope and window, newest first."
        action={<RuntimeOutcomeFilter value={state} onChange={setState} />}
        flush
      >
        <PaginatedTable
          rows={query.data?.runs ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => (
            <EntityTable
              headers={["Run", "Job", "Outcome", "Type", "Engine", "Started", "Duration"]}
              rows={rows}
              rowKey={(run) => run.run_id}
              onClick={(run) => onDetail({ kind: "job_run", item: run })}
              mono={[0]}
              emptyLabel="No job runs were recorded for this scope and window."
              cells={(run) => [
                run.run_id,
                run.job_name,
                <RuntimeOutcomeBadge
                  key="state"
                  state={run.state}
                  hasEnded={Boolean(run.ended_at)}
                  misfireReason={run.misfire_reason}
                />,
                <ExecutionTypeBadge key="type" value={run.execution_type} />,
                run.engine_name ?? "-",
                <RelativeTime key="started" value={run.started_at} />,
                run.ended_at ? formatDuration(run.duration_ms) : "never finished",
              ]}
            />
          )}
        />
      </Panel>
    </QueryState>
  );
}

/** What the task was deployed onto, named rather than reduced to one member.
 *
 * A clustered route is one deployment whose engines are a property of it. Naming
 * a single member there is what made a two-engine cluster read as one engine,
 * and it was the SILENT engine that won, because the rows were picked by
 * whichever started last. */
function deploymentEngine(deployment: RuntimeDeployment): string {
  const environment = deployment.environment_name ?? "-";
  if (deployment.deployment_target !== "cluster" || deployment.members.length === 0) {
    return `${environment} / ${deployment.engine_name ?? "-"}`;
  }
  const engines = deployment.members
    .map((member) => member.engine_name ?? member.engine_id ?? "?")
    .join(", ");
  return `${environment} / ${deployment.engine_name ?? "cluster"} (${engines})`;
}

/** Telemetry coverage, stated instead of implied.
 *
 * An aggregate summed over one of two cluster members is a floor, not a total,
 * and nothing in the number itself says so. "Not supplied" for the whole row
 * when one engine simply never reports is the same failure in the other
 * direction: it reads as no traffic rather than as not measured. */
function deploymentCoverage(deployment: RuntimeDeployment): string {
  if (deployment.deployment_target !== "cluster" || deployment.engines_total === 0) {
    return "";
  }
  const silent = deployment.silent_engines.join(", ");
  // Seeing ONE node of something deployed to a cluster is itself the finding,
  // and it is the commonest shape on a live estate: 22 of 60 rows are a single
  // observed member that reported nothing. Suppressing the note below two
  // engines left exactly those rows as a bare "Not supplied", which reads as no
  // traffic -- the misreading this whole column exists to prevent.
  if (deployment.engines_total === 1) {
    const only = deployment.members[0]?.engine_name ?? deployment.members[0]?.engine_id ?? "one engine";
    return deployment.engines_reporting === 0
      ? ` · only ${only} of this cluster observed, and it supplies no telemetry`
      : ` · only ${only} of this cluster observed`;
  }
  if (deployment.engines_reporting === deployment.engines_total) {
    return "";
  }
  return ` · measured on ${deployment.engines_reporting} of ${deployment.engines_total} engines${
    silent ? ` (no telemetry from ${silent})` : ""
  }`;
}

/** Legible label for every state value, whether the deployment is still open or not. */
const DEPLOYMENT_OUTCOME_LABELS: Partial<Record<RuntimeExecutionState, string>> = {
  failed: "failed",
  misfired: "never started",
  timed_out: "timed out",
  terminated: "terminated",
};

function deploymentState(deployment: RuntimeDeployment): string {
  if (!deployment.deployment_id || !deployment.state || !deployment.started_at) {
    return "never deployed";
  }
  const label = DEPLOYMENT_OUTCOME_LABELS[deployment.state];
  if (!deployment.ended_at) {
    return label ? `${label === "failed" ? "failed to deploy" : label}` : "deployed";
  }
  return label ?? "undeployed";
}

function Deployments({
  scope,
  kind,
  onDetail,
}: ViewProps & { kind: "route" | "data_service" }) {
  const [page, setPage] = useState(0);
  const [state, setState] = useState<"" | RuntimeExecutionState>("");
  useEffect(() => setPage(0), [scope, kind, state]);
  // `/runtime/deployments` already accepts `state` server-side
  // (ListRuntimeDeploymentsInput.state), so this filters the same paginated
  // query rather than post-filtering a page client-side.
  const query = useRuntimeDeployments({
    projectId: scope,
    executableKind: kind,
    state: state || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const noun = kind === "route" ? "Route" : "Data service";

  return (
    <QueryState queries={[query]}>
      <Panel
        title={`${noun} deployments`}
        icon={kind === "route" ? Activity : Server}
        description={`Latest deployment lifecycle and ${kind === "route" ? "Camel exchange" : "service"} telemetry. An open deployment is healthy long-running work, not a job that never finished.`}
        action={<RuntimeOutcomeFilter value={state} onChange={setState} />}
        flush
      >
        <PaginatedTable
          rows={query.data?.deployments ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => (
            <EntityTable
              headers={[
                noun,
                "Environment / engine",
                "Deployment",
                kind === "route" ? "Exchanges" : "Calls",
                kind === "route" ? "Process time" : "Request rates",
                "Metrics",
              ]}
              rows={rows}
              rowKey={(deployment) =>
                deployment.deployment_id ??
                `${deployment.source_key}:${deployment.executable_id}:${deployment.engine_name ?? ""}`
              }
              onClick={(deployment) => onDetail({ kind: "deployment", item: deployment })}
              cells={(deployment) => [
                deployment.executable_name,
                deploymentEngine(deployment),
                `${deploymentState(deployment)}${deployment.started_at ? ` · ${deployment.ended_at ? formatDuration(deployment.duration_ms) : `since ${formatAge(Date.now() - new Date(deployment.started_at).getTime())}`}` : ""}${deployment.artifact_version ? ` · v${deployment.artifact_version}` : ""}`,
                kind === "route"
                  ? deployment.total_exchanges == null
                    ? `Not supplied${deploymentCoverage(deployment)}`
                    : `${formatCompact(deployment.completed_exchanges ?? 0)} completed · ${formatCompact(deployment.failed_exchanges ?? 0)} failed · ${formatCompact(deployment.total_exchanges)} total${deploymentCoverage(deployment)}`
                  : deployment.service_call_count == null
                    ? `Not supplied${deploymentCoverage(deployment)}`
                    : `${formatCompact(deployment.service_call_count)} calls${deploymentCoverage(deployment)}`,
                kind === "route"
                  ? deployment.mean_process_time_ms == null && deployment.last_process_time_ms == null
                    ? "Not supplied"
                    : `last ${formatDuration(deployment.last_process_time_ms)} · mean ${formatDuration(deployment.mean_process_time_ms)}`
                  : deployment.one_minute_rate == null && deployment.five_minute_rate == null
                    ? "Not supplied"
                    : `1m ${(deployment.one_minute_rate ?? 0).toFixed(2)} · 5m ${(deployment.five_minute_rate ?? 0).toFixed(2)} · 15m ${(deployment.fifteen_minute_rate ?? 0).toFixed(2)}`,
                deployment.metrics_updated_at
                  ? `${formatAge(Date.now() - new Date(deployment.metrics_updated_at).getTime())} ago · ${deployment.metric_path ?? "default path"}`
                  : deployment.metric_path
                    ? `Configured · ${deployment.metric_path}`
                    : "Not configured / supplied",
              ]}
              emptyLabel={`No ${noun.toLowerCase()} deployments were observed in this scope.`}
            />
          )}
        />
      </Panel>
    </QueryState>
  );
}

function Plans({ scope, since, onDetail }: ViewProps) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [scope]);
  const query = useRuntimePlans({ projectId: scope, limit: PAGE_SIZE, offset: page * PAGE_SIZE });

  return (
    <div className="flex flex-col gap-6">
      <PlanRunsPanel scope={scope} since={since} onDetail={onDetail} />
      <QueryState queries={[query]}>
        <Panel
          title="Plans"
          icon={ListTree}
          description="Orchestrations that chain jobs into a sequence of steps."
          flush
        >
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
    </div>
  );
}

/**
 * Per-run listing for plans, filterable by outcome. `/runtime/plan-runs`
 * already accepts `state` server-side (ListRuntimePlanRunsInput.state). A
 * plan run has no single execution (it fans out into `step_results`), so
 * clicking a row opens the generic field list rather than a log viewer.
 */
function PlanRunsPanel({ scope, since, onDetail }: Pick<ViewProps, "scope" | "since" | "onDetail">) {
  const [page, setPage] = useState(0);
  const [state, setState] = useState<"" | RuntimeExecutionState>("");
  useEffect(() => setPage(0), [scope, since, state]);
  const query = useRuntimePlanRuns({
    projectId: scope,
    since,
    state: state || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  return (
    <QueryState queries={[query]}>
      <Panel
        title="Plan runs"
        icon={ScrollText}
        description="Every observed run for plans in this scope and window, newest first."
        action={<RuntimeOutcomeFilter value={state} onChange={setState} />}
        flush
      >
        <PaginatedTable
          rows={query.data?.runs ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => (
            <EntityTable
              headers={["Run", "Plan", "Outcome", "Type", "Started", "Duration"]}
              rows={rows}
              rowKey={(run) => run.run_id}
              onClick={(run) => onDetail({ kind: "plan_run", item: run })}
              mono={[0]}
              emptyLabel="No plan runs were recorded for this scope and window."
              cells={(run) => [
                run.run_id,
                run.plan_name,
                <RuntimeOutcomeBadge
                  key="state"
                  state={run.state}
                  hasEnded={Boolean(run.ended_at)}
                  misfireReason={run.misfire_reason}
                />,
                <ExecutionTypeBadge key="type" value={run.execution_type} />,
                <RelativeTime key="started" value={run.started_at} />,
                run.ended_at ? formatDuration(run.duration_ms) : "never finished",
              ]}
            />
          )}
        />
      </Panel>
    </QueryState>
  );
}

// GAP: `/runtime/incidents` has no `state`/outcome query parameter today
// (only project_id, since, until, limit, offset -- see
// backend/src/api/routes/runtime.py::list_incidents). An incident already
// carries a `state` field, but filtering it here would mean fetching one
// server-paginated page and hiding rows from it client-side, which would
// silently misreport `total`. No outcome filter is mounted on this view
// until the backend accepts one; do not add client-side filtering as a
// workaround.
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
        icon={AlertTriangle}
        description="A likely cause is an inference, not a confirmed diagnosis. Confidence is shown for every row."
        flush
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
              emptyLabel="No incidents were derived for this scope and window."
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
                <RelativeTime key="first-bad" value={incident.first_bad_at} />,
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
      <div className="grid gap-5 xl:grid-cols-2">
        <Panel
          title="Runtime sources"
          icon={Server}
          description="Where imported runtime data comes from, and when each last delivered."
          flush
        >
          <EntityTable
            headers={["Source", "Type", "Status", "Last sync"]}
            rows={sources.data?.sources ?? []}
            rowKey={(source) => `${source.estate_key}:${source.source_key}`}
            onClick={(source) => onDetail({ kind: "source", item: source })}
            emptyLabel="No runtime sources are registered for this scope."
            cells={(source) => [
              source.label,
              source.source_type,
              source.status,
              <RelativeTime key="synced" value={source.last_synced_at} />,
            ]}
          />
        </Panel>
        <Panel
          title="Engines"
          icon={Network}
          description="Execution hosts the source reports, with their reported health."
          flush
        >
          <EntityTable
            headers={["Engine", "Environment", "Version", "Status"]}
            rows={engines.data?.engines ?? []}
            rowKey={(engine) => engine.engine_id}
            onClick={(engine) => onDetail({ kind: "engine", item: engine })}
            emptyLabel="No engines were reported for this scope."
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
      className="rounded-xl border border-border bg-gradient-to-br from-primary/[0.04] to-transparent p-5 shadow-elevation-sm"
    >
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Sparkles className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">Ask these logs</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Nexus answers from the captured log lines and this project&apos;s generated
            documentation, and cites the runs it used.
          </p>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <Input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="What changed in the failed runs?"
              aria-label="Ask these logs"
              className="sm:flex-1"
            />
            <Button type="submit" size="lg" disabled={!question.trim()} isLoading={sending}>
              {sending ? "Opening chat" : "Ask Nexus"}
              {!sending && <ArrowRight className="h-4 w-4" aria-hidden />}
            </Button>
          </div>
        </div>
      </div>
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

  // Runs/incidents use per-execution diagnostics. Deployments expose only the
  // captured shared-engine log because it is not safe to derive a Route cause.
  // A job run mirrors one execution 1:1, so it resolves the same way; a plan
  // run has no single execution (it fans out into `step_results`), so it
  // falls through to `null` and the generic field list is all it shows.
  const executionId =
    detail.kind === "execution"
      ? detail.item.execution_id
      : detail.kind === "job_run"
        ? detail.item.execution_id ?? null
        : detail.kind === "incident"
          ? detail.item.execution_id
          : detail.kind === "deployment"
            ? detail.item.deployment_id ?? null
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
        className="flex h-full w-full max-w-2xl flex-col border-l border-border bg-background shadow-elevation-lg sm:rounded-xl sm:border"
      >
        <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {humanize(detail.kind)}
            </p>
            <h2 className="mt-0.5 truncate font-mono text-sm font-semibold text-foreground">
              {detailTitle(detail, item as Record<string, unknown>)}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-md p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label="Close details"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="flex min-w-0 flex-col gap-6">
            <DetailFields item={item as Record<string, unknown>} />
            {executionId && detail.kind !== "deployment" && (
              <RuntimeInsightPanel executionId={executionId} />
            )}
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

  // A curated read of `state` up front -- "misfired" must not register as a
  // failure at a glance -- while every raw field (state, raw_state,
  // misfire_reason, execution_type) still appears verbatim in the grid below.
  const state = typeof item.state === "string" ? item.state : null;

  return (
    <div className="flex min-w-0 flex-col gap-5">
      {state && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-muted/20 px-4 py-3">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Outcome</p>
            <div className="mt-1">
              <RuntimeOutcomeBadge
                state={state}
                hasEnded={Boolean(item.ended_at)}
                misfireReason={typeof item.misfire_reason === "string" ? item.misfire_reason : null}
              />
            </div>
          </div>
          {typeof item.raw_state === "string" && item.raw_state !== state && (
            <p className="text-xs text-muted-foreground">
              Reported by source as <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">{item.raw_state}</code>
            </p>
          )}
        </div>
      )}
      <dl className="grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2">
        {scalars.map(([key, value]) => (
          <div key={key} className="bg-card px-3 py-2.5">
            <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {humanize(key)}
            </dt>
            <dd className="mt-0.5 break-words text-sm text-foreground">{String(value)}</dd>
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
          ? ["Job", "Outcome", "Started"]
          : ["Run", "Job", "Outcome", "Type", "Started", "Duration"]
      }
      rows={rows}
      rowKey={(run) => run.execution_id}
      onClick={(run) => onDetail({ kind: "execution", item: run })}
      // The execution id is a copy-into-a-ticket value, so it gets monospace.
      mono={compact ? undefined : [0]}
      emptyLabel="No runs were recorded for this scope and window."
      cells={(run) =>
        compact
          ? [
              run.job_name,
              <StateCell key="state" run={run} />,
              <RelativeTime key="started" value={run.started_at} />,
            ]
          : [
              run.execution_id,
              run.job_name,
              <StateCell key="state" run={run} />,
              <ExecutionTypeBadge key="type" value={run.execution_type} />,
              <RelativeTime key="started" value={run.started_at} />,
              run.ended_at ? formatDuration(run.duration_ms) : "never finished",
            ]
      }
    />
  );
}

/** Outcome is state: icon + label, so it never rests on colour alone. */
function StateCell({ run }: { run: RuntimeExecution }) {
  return (
    <RuntimeOutcomeBadge
      state={run.state}
      hasEnded={Boolean(run.ended_at)}
      misfireReason={run.misfire_reason}
    />
  );
}

function detailTitle(detail: Detail, item: Record<string, unknown>) {
  return String(
      item.execution_id ??
      item.run_id ??
      item.deployment_id ??
      item.executable_id ??
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
// The computation lives in `useSyncFreshness` so the header pill cannot
// disagree with this banner.
function SyncStatusBanner({ freshness }: { freshness: SyncFreshness | null }) {
  if (!freshness) return null;

  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-xl border px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between",
        freshness.isStale
          ? "border-amber-500/40 bg-amber-500/5 text-amber-700 dark:text-amber-400"
          : "border-border bg-muted/30 text-muted-foreground",
      )}
    >
      <span className="flex items-center gap-2">
        {freshness.isStale ? (
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
        ) : (
          <RefreshCw className="h-4 w-4 shrink-0" aria-hidden />
        )}
        <span>{freshness.message}</span>
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
