"use client";

import { useState } from "react";
import { RefreshCw, AlertTriangle, Activity } from "lucide-react";
import {
  useRuntimeExecution,
  useRuntimeExecutions,
  useRuntimeArtifacts,
  useRuntimeConnections,
  useRuntimeDependencies,
  useRuntimeDependencySummary,
  useRuntimeEngineHealth,
  useRuntimeEngines,
  useRuntimeFailures,
  useRuntimeHealth,
  useRuntimeJobHealth,
  useRuntimeJobRuns,
  useRuntimeJobs,
  useRuntimeOverview,
  useRuntimePlanHealth,
  useRuntimePlanRuns,
  useRuntimePlans,
  useRuntimeSources,
} from "@/hooks/useRuntimeLogs";
import { useUserProjects } from "@/hooks/useUserProjects";
import { RuntimeInsightPanel } from "@/components/runtime/RuntimeInsightPanel";
import type {
  RuntimeExecution,
  RuntimeExecutionState,
  RuntimeEngine,
  RuntimeEngineHealth,
  RuntimeEstateOverview,
  RuntimeHealthSummary,
  RuntimeArtifact,
  RuntimeConnection,
  RuntimeDependencySummary,
  RuntimeJobDependency,
  RuntimeJob,
  RuntimeJobHealth,
  RuntimeJobRun,
  RuntimePlan,
  RuntimePlanHealth,
  RuntimePlanRun,
  RuntimeSource,
} from "@/types";
import { cn } from "@/lib/utils";

type View = "jobs" | "runs" | "plans" | "plan-runs" | "executions" | "failures";

const STATE_OPTIONS: { value: "" | RuntimeExecutionState; label: string }[] = [
  { value: "", label: "All states" },
  { value: "success", label: "Success" },
  { value: "failed", label: "Failed" },
  { value: "running", label: "Running" },
  { value: "unknown", label: "Unknown" },
];

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function StateBadge({ state }: { state: RuntimeExecutionState }) {
  const styles: Record<RuntimeExecutionState, string> = {
    success: "bg-green-100 text-green-700",
    failed: "bg-red-100 text-red-700",
    running: "bg-blue-100 text-blue-700",
    unknown: "bg-gray-100 text-gray-600",
  };
  return (
    <span className={cn("px-2 py-0.5 rounded-full text-xs font-medium", styles[state])}>
      {state}
    </span>
  );
}

export default function RuntimeLogsPage() {
  const [view, setView] = useState<View>("jobs");
  const [stateFilter, setStateFilter] = useState<"" | RuntimeExecutionState>("");
  const [jobName, setJobName] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<string>("");

  const projects = useUserProjects();
  const executions = useRuntimeExecutions({
    state: stateFilter || undefined,
    jobName: jobName.trim() || undefined,
    projectId: projectId || undefined,
  });
  const failures = useRuntimeFailures({ projectId: projectId || undefined });
  const health = useRuntimeHealth({ projectId: projectId || undefined, limit: 5 });
  const sources = useRuntimeSources({ projectId: projectId || undefined });
  const engines = useRuntimeEngines({ projectId: projectId || undefined });
  const engineHealth = useRuntimeEngineHealth({ projectId: projectId || undefined });
  const overview = useRuntimeOverview({ projectId: projectId || undefined });
  const jobs = useRuntimeJobs({ projectId: projectId || undefined });
  const artifacts = useRuntimeArtifacts({ projectId: projectId || undefined, limit: 20 });
  const connections = useRuntimeConnections({ projectId: projectId || undefined, limit: 20 });
  const dependencies = useRuntimeDependencies({ projectId: projectId || undefined, limit: 20 });
  const dependencySummary = useRuntimeDependencySummary({ projectId: projectId || undefined });
  const jobRuns = useRuntimeJobRuns({ projectId: projectId || undefined, limit: 100 });
  const jobHealth = useRuntimeJobHealth({ projectId: projectId || undefined });
  const plans = useRuntimePlans({ projectId: projectId || undefined });
  const planRuns = useRuntimePlanRuns({ projectId: projectId || undefined, limit: 100 });
  const planHealth = useRuntimePlanHealth({ projectId: projectId || undefined });
  const detail = useRuntimeExecution(selectedId, selectedId !== null);

  function handleProjectChange(id: string) {
    setProjectId(id);
    setSelectedId(null);
  }

  function refresh() {
    executions.refetch();
    failures.refetch();
    health.refetch();
    sources.refetch();
    engines.refetch();
    engineHealth.refetch();
    overview.refetch();
    jobs.refetch();
    artifacts.refetch();
    connections.refetch();
    dependencies.refetch();
    dependencySummary.refetch();
    jobRuns.refetch();
    jobHealth.refetch();
    plans.refetch();
    planRuns.refetch();
    planHealth.refetch();
    if (selectedId) {
      detail.refetch();
    }
  }

  return (
    <div className="max-w-7xl mx-auto py-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Activity className="h-6 w-6" />
          Runtime Operations
        </h1>
        <button
          onClick={refresh}
          className="flex items-center gap-2 px-3 py-2 border rounded-md text-sm hover:bg-muted transition-colors"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* View tabs */}
      <div className="border-b">
        <nav className="flex gap-6">
          {(["jobs", "runs", "plans", "plan-runs", "executions", "failures"] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={cn(
                "pb-2 text-sm font-medium border-b-2 -mb-px capitalize transition-colors",
                view === v
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {v}
            </button>
          ))}
        </nav>
      </div>

      {/* Project filter — applies to both tabs */}
      {projects.data && projects.data.projects.length > 1 && (
        <div className="flex items-center gap-2">
          <select
            value={projectId}
            onChange={(e) => handleProjectChange(e.target.value)}
            className="px-3 py-2 border rounded-md text-sm bg-background"
            aria-label="Filter by project"
          >
            <option value="">All projects</option>
            {projects.data.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name}
              </option>
            ))}
          </select>
        </div>
      )}

      <RuntimeEstatePanel sources={sources} engines={engines} overview={overview} />

      <OperationsHealthPanel
        engineHealth={engineHealth}
        jobHealth={jobHealth}
        planHealth={planHealth}
        overview={overview}
      />

      <DependencyMetadataPanel
        artifacts={artifacts}
        connections={connections}
        dependencies={dependencies}
        summary={dependencySummary}
      />

      <HealthPanel query={health} />

      {view === "jobs" ? (
        <JobsView query={jobs} health={jobHealth} />
      ) : view === "runs" ? (
        <RunsView query={jobRuns} selectedId={selectedId} onSelect={setSelectedId} />
      ) : view === "plans" ? (
        <PlansView query={plans} health={planHealth} />
      ) : view === "plan-runs" ? (
        <PlanRunsView query={planRuns} />
      ) : view === "executions" ? (
        <ExecutionsView
          query={executions}
          stateFilter={stateFilter}
          onStateFilter={setStateFilter}
          jobName={jobName}
          onJobName={setJobName}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      ) : (
        <FailuresView query={failures} selectedId={selectedId} onSelect={setSelectedId} />
      )}

      {selectedId && (
        <DiagnosisDetailPanel
          executionId={selectedId}
          detail={detail.data?.execution ?? null}
          detailLoading={detail.isLoading}
          detailError={detail.isError}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}

function RuntimeEstatePanel({
  sources,
  engines,
  overview,
}: {
  sources: ReturnType<typeof useRuntimeSources>;
  engines: ReturnType<typeof useRuntimeEngines>;
  overview: ReturnType<typeof useRuntimeOverview>;
}) {
  if (sources.isLoading || engines.isLoading || overview.isLoading) {
    return <StatusBox>Loading runtime estate…</StatusBox>;
  }
  if (sources.isError || engines.isError || overview.isError) {
    return <StatusBox error>Failed to load runtime estate.</StatusBox>;
  }

  const sourceRows = sources.data?.sources ?? [];
  const engineRows = engines.data?.engines ?? [];
  const summary = overview.data?.overview ?? null;

  if (sourceRows.length === 0 && engineRows.length === 0) {
    return (
      <StatusBox>
        No runtime source or engine data yet. Connect a runtime source or run the demo seed.
      </StatusBox>
    );
  }

  return (
    <div className="space-y-3">
      {summary && <OverviewStrip overview={summary} />}
      <div className="grid lg:grid-cols-[minmax(18rem,0.8fr)_minmax(0,1.2fr)] gap-3">
        <SourcesPanel sources={sourceRows} />
        <EnginesPanel engines={engineRows} />
      </div>
    </div>
  );
}

function OverviewStrip({ overview }: { overview: RuntimeEstateOverview }) {
  const stats = [
    { label: "Sources", value: overview.sources_total },
    { label: "Demo", value: overview.sources_demo },
    { label: "Engines", value: overview.engines_total },
    { label: "Online", value: overview.engines_online },
    { label: "Degraded", value: overview.engines_degraded },
    { label: "Offline", value: overview.engines_offline },
    { label: "Unhealthy", value: overview.engines_unhealthy + overview.jobs_unhealthy + overview.plans_unhealthy },
    { label: "Stale sources", value: overview.sources_stale },
    { label: "Environments", value: overview.environments_total },
    { label: "Workspaces", value: overview.workspaces_total },
  ];
  return (
    <div className="border rounded-lg px-3 py-2">
      <div className="grid grid-cols-2 sm:grid-cols-5 lg:grid-cols-10 gap-2">
        {stats.map((stat) => (
          <div key={stat.label} className="min-w-0">
            <p className="text-xs text-muted-foreground">{stat.label}</p>
            <p className="text-lg font-semibold tabular-nums">{stat.value}</p>
          </div>
        ))}
      </div>
      {overview.last_synced_at && (
        <p className="text-xs text-muted-foreground mt-1">
          Freshness: {formatTimestamp(overview.last_synced_at)}
        </p>
      )}
    </div>
  );
}

function OperationsHealthPanel({
  engineHealth,
  jobHealth,
  planHealth,
  overview,
}: {
  engineHealth: ReturnType<typeof useRuntimeEngineHealth>;
  jobHealth: ReturnType<typeof useRuntimeJobHealth>;
  planHealth: ReturnType<typeof useRuntimePlanHealth>;
  overview: ReturnType<typeof useRuntimeOverview>;
}) {
  if (engineHealth.isLoading || jobHealth.isLoading || planHealth.isLoading || overview.isLoading) {
    return <StatusBox>Loading operational health…</StatusBox>;
  }
  if (engineHealth.isError || jobHealth.isError || planHealth.isError || overview.isError) {
    return <StatusBox error>Failed to load operational health.</StatusBox>;
  }

  const unhealthyEngines = (engineHealth.data?.health ?? []).filter(isUnhealthy).slice(0, 3);
  const unhealthyJobs = (jobHealth.data?.health ?? []).filter(isUnhealthy).slice(0, 3);
  const unhealthyPlans = (planHealth.data?.health ?? []).filter(isUnhealthy).slice(0, 3);
  const staleSources = overview.data?.overview.sources_stale ?? 0;

  if (
    unhealthyEngines.length === 0 &&
    unhealthyJobs.length === 0 &&
    unhealthyPlans.length === 0 &&
    staleSources === 0
  ) {
    return <StatusBox>No unhealthy engines, jobs, plans, or stale sources recorded.</StatusBox>;
  }

  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="px-3 py-2 border-b bg-muted/40 flex items-center justify-between gap-3">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Operational Health
        </p>
        {staleSources > 0 && (
          <span className="text-xs rounded px-1.5 py-0.5 bg-amber-100 text-amber-700">
            {staleSources} stale source{staleSources === 1 ? "" : "s"}
          </span>
        )}
      </div>
      <div className="grid lg:grid-cols-3 divide-y lg:divide-y-0 lg:divide-x">
        <HealthColumn
          title="Engines"
          emptyLabel="No unhealthy engines."
          rows={unhealthyEngines}
          getKey={(item) => `${item.source_key}:${item.engine_id}`}
          getName={(item) => item.engine_name}
          getMeta={(item) => item.environment_name ?? "unknown environment"}
        />
        <HealthColumn
          title="Jobs"
          emptyLabel="No unhealthy jobs."
          rows={unhealthyJobs}
          getKey={(item) => `${item.source_key}:${item.job_id}`}
          getName={(item) => item.job_name}
          getMeta={(item) => item.engine_name ?? "unknown engine"}
        />
        <HealthColumn
          title="Plans"
          emptyLabel="No unhealthy plans."
          rows={unhealthyPlans}
          getKey={(item) => `${item.source_key}:${item.plan_id}`}
          getName={(item) => item.plan_name}
          getMeta={(item) => item.workspace_name ?? "unknown workspace"}
        />
      </div>
    </div>
  );
}

function DependencyMetadataPanel({
  artifacts,
  connections,
  dependencies,
  summary,
}: {
  artifacts: ReturnType<typeof useRuntimeArtifacts>;
  connections: ReturnType<typeof useRuntimeConnections>;
  dependencies: ReturnType<typeof useRuntimeDependencies>;
  summary: ReturnType<typeof useRuntimeDependencySummary>;
}) {
  if (artifacts.isLoading || connections.isLoading || dependencies.isLoading || summary.isLoading) {
    return <StatusBox>Loading dependency metadata…</StatusBox>;
  }
  if (artifacts.isError || connections.isError || dependencies.isError || summary.isError) {
    return <StatusBox error>Failed to load dependency metadata.</StatusBox>;
  }

  const artifactRows = artifacts.data?.artifacts ?? [];
  const connectionRows = connections.data?.connections ?? [];
  const dependencyRows = dependencies.data?.dependencies ?? [];
  const summaryValue = summary.data?.summary ?? null;

  if (!summaryValue || (artifactRows.length === 0 && connectionRows.length === 0 && dependencyRows.length === 0)) {
    return (
      <StatusBox>
        No extended dependency metadata recorded. Artifacts, connections, schedules, promotions,
        and clusters are unavailable for the selected runtime data.
      </StatusBox>
    );
  }

  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="px-3 py-2 border-b bg-muted/40 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Dependency Metadata
        </p>
        <UnavailableMetadata summary={summaryValue} />
      </div>
      <div className="grid sm:grid-cols-3 divide-y sm:divide-y-0 sm:divide-x">
        <MetadataCount label="Artifacts" value={summaryValue.artifacts_total} />
        <MetadataCount label="Connections" value={summaryValue.connections_total} />
        <MetadataCount label="Dependency edges" value={summaryValue.dependencies_total} />
      </div>
      <div className="grid lg:grid-cols-3 divide-y lg:divide-y-0 lg:divide-x">
        <MetadataList
          title="Connections"
          emptyLabel="No connections recorded."
          rows={connectionRows.slice(0, 5)}
          render={(item: RuntimeConnection) => (
            <>
              <p className="font-medium truncate" title={item.name}>{item.name}</p>
              <p className="text-xs text-muted-foreground truncate">
                {item.connection_type ?? "unknown type"} · {item.workspace_name ?? "unknown workspace"}
              </p>
            </>
          )}
        />
        <MetadataList
          title="Artifacts"
          emptyLabel="No artifacts recorded."
          rows={artifactRows.slice(0, 5)}
          render={(item: RuntimeArtifact) => (
            <>
              <p className="font-medium truncate" title={item.name}>{item.name}</p>
              <p className="text-xs text-muted-foreground truncate">
                {item.artifact_type ?? "unknown type"} · {item.version ?? "unknown version"}
              </p>
            </>
          )}
        />
        <MetadataList
          title="Usage"
          emptyLabel="No dependency usage edges recorded."
          rows={dependencyRows.slice(0, 5)}
          render={(item: RuntimeJobDependency) => (
            <>
              <p className="font-medium truncate" title={item.job_name}>{item.job_name}</p>
              <p className="text-xs text-muted-foreground truncate">
                {item.dependency_type}: {item.dependency_name ?? item.dependency_id}
              </p>
            </>
          )}
        />
      </div>
    </div>
  );
}

function MetadataCount({ label, value }: { label: string; value: number }) {
  return (
    <div className="px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function MetadataList<T>({
  title,
  emptyLabel,
  rows,
  render,
}: {
  title: string;
  emptyLabel: string;
  rows: T[];
  render: (item: T) => React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <div className="px-3 py-2 border-b">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {title}
        </p>
      </div>
      {rows.length === 0 ? (
        <p className="px-3 py-3 text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <div className="divide-y">
          {rows.map((item, index) => (
            <div key={index} className="px-3 py-2 text-sm min-w-0">
              {render(item)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function UnavailableMetadata({ summary }: { summary: RuntimeDependencySummary }) {
  const unavailable = summary.availability.unavailable;
  if (unavailable.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {unavailable.map((item) => (
        <span
          key={item}
          className="text-xs rounded px-1.5 py-0.5 bg-gray-100 text-gray-600"
          title="This metadata is not available from the current runtime mirror."
        >
          {item} unavailable
        </span>
      ))}
    </div>
  );
}

function HealthColumn<T extends RuntimeEngineHealth | RuntimeJobHealth | RuntimePlanHealth>({
  title,
  emptyLabel,
  rows,
  getKey,
  getName,
  getMeta,
}: {
  title: string;
  emptyLabel: string;
  rows: T[];
  getKey: (item: T) => string;
  getName: (item: T) => string;
  getMeta: (item: T) => string;
}) {
  return (
    <div className="min-w-0">
      <div className="px-3 py-2 border-b lg:border-b-0">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {title}
        </p>
      </div>
      {rows.length === 0 ? (
        <p className="px-3 pb-3 text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <div className="divide-y">
          {rows.map((item) => (
            <HealthEntityRow
              key={getKey(item)}
              name={getName(item)}
              meta={getMeta(item)}
              failedRuns={item.failed_runs}
              totalRuns={item.total_runs}
              failureRate={item.failure_rate}
              streak={item.current_failure_streak}
              latestRunAt={item.latest_run_at}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function HealthEntityRow({
  name,
  meta,
  failedRuns,
  totalRuns,
  failureRate,
  streak,
  latestRunAt,
}: {
  name: string;
  meta: string;
  failedRuns: number;
  totalRuns: number;
  failureRate: number;
  streak: number;
  latestRunAt?: string | null;
}) {
  return (
    <div className="px-3 py-2 text-sm min-w-0">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium truncate" title={name}>{name}</p>
        <span className="text-xs rounded px-1.5 py-0.5 bg-red-100 text-red-700">
          {Math.round(failureRate * 100)}%
        </span>
      </div>
      <p className="text-xs text-muted-foreground truncate" title={meta}>{meta}</p>
      <p className="text-xs text-muted-foreground">
        {failedRuns}/{totalRuns} failed{streak > 0 ? ` · streak ${streak}` : ""}
        {latestRunAt ? ` · ${formatTimestamp(latestRunAt)}` : ""}
      </p>
    </div>
  );
}

function isUnhealthy(
  item: RuntimeEngineHealth | RuntimeJobHealth | RuntimePlanHealth,
): boolean {
  return item.failed_runs > 0 || item.current_failure_streak > 0;
}

function SourcesPanel({ sources }: { sources: RuntimeSource[] }) {
  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="px-3 py-2 border-b bg-muted/40">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Runtime Sources
        </p>
      </div>
      {sources.length === 0 ? (
        <div className="px-3 py-4 text-sm text-muted-foreground">
          No runtime sources found.
        </div>
      ) : (
        <div className="divide-y">
          {sources.map((source) => (
            <div key={`${source.project_id}:${source.source_key}`} className="px-3 py-2 text-sm">
              <div className="flex items-center justify-between gap-2">
                <p className="font-medium truncate" title={source.label}>{source.label}</p>
                <span className={cn("px-1.5 py-0.5 rounded text-xs", sourceStatusClass(source.status))}>
                  {source.status}
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                {source.source_key} · {source.source_type}
                {source.is_demo ? " · demo data" : ""}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Last sync: {source.last_synced_at ? formatTimestamp(source.last_synced_at) : "unknown"}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EnginesPanel({ engines }: { engines: RuntimeEngine[] }) {
  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="px-3 py-2 border-b bg-muted/40">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Runtime Engines
        </p>
      </div>
      {engines.length === 0 ? (
        <div className="px-3 py-4 text-sm text-muted-foreground">
          No runtime engines found.
        </div>
      ) : (
        <table className="w-full text-sm table-fixed">
          <thead className="bg-muted/30 text-xs uppercase text-muted-foreground">
            <tr>
              <th className="text-left font-medium px-3 py-2 w-[28%]">Engine</th>
              <th className="text-left font-medium px-3 py-2 w-[15%]">Status</th>
              <th className="text-left font-medium px-3 py-2 w-[20%]">Environment</th>
              <th className="text-left font-medium px-3 py-2 w-[13%]">Version</th>
              <th className="text-left font-medium px-3 py-2 w-[24%]">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {engines.map((engine) => (
              <tr key={`${engine.source_key}:${engine.engine_id}`} className="border-t">
                <td className="px-3 py-2 min-w-0">
                  <p className="font-medium truncate" title={engine.name}>{engine.name}</p>
                  <p className="text-xs text-muted-foreground truncate" title={engine.engine_id}>
                    {engine.source_key} · {engine.engine_id}
                  </p>
                </td>
                <td className="px-3 py-2">
                  <span className={cn("px-1.5 py-0.5 rounded text-xs", engineStatusClass(engine.status))}>
                    {engine.status}
                  </span>
                </td>
                <td className="px-3 py-2 truncate" title={engine.environment_name ?? undefined}>
                  {engine.environment_name ?? "unknown"}
                </td>
                <td className="px-3 py-2 truncate">{engine.version ?? "—"}</td>
                <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
                  {engine.last_seen_at ? formatTimestamp(engine.last_seen_at) : "unknown"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function sourceStatusClass(status: RuntimeSource["status"]): string {
  if (status === "success") return "bg-green-100 text-green-700";
  if (status === "partial") return "bg-amber-100 text-amber-700";
  return "bg-red-100 text-red-700";
}

function engineStatusClass(status: RuntimeEngine["status"]): string {
  const styles: Record<RuntimeEngine["status"], string> = {
    online: "bg-green-100 text-green-700",
    degraded: "bg-amber-100 text-amber-700",
    offline: "bg-red-100 text-red-700",
    unknown: "bg-gray-100 text-gray-700",
    stale: "bg-slate-100 text-slate-700",
  };
  return styles[status];
}

function HealthPanel({ query }: { query: ReturnType<typeof useRuntimeHealth> }) {
  if (query.isLoading) return <StatusBox>Loading runtime health…</StatusBox>;
  if (query.isError) return <StatusBox error>Failed to load runtime health.</StatusBox>;
  const rows = query.data?.health ?? [];
  if (rows.length === 0) {
    return <StatusBox>No runtime health data yet.</StatusBox>;
  }
  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="px-3 py-2 border-b bg-muted/40">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Unhealthy Components
        </p>
      </div>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 divide-y sm:divide-y-0 sm:divide-x">
        {rows.map((item) => (
          <HealthItem key={`${item.project_id}:${item.source_key ?? ""}:${item.job_name}`} item={item} />
        ))}
      </div>
    </div>
  );
}

function HealthItem({ item }: { item: RuntimeHealthSummary }) {
  const failurePct = `${Math.round(item.failure_rate * 100)}%`;
  return (
    <div className="px-3 py-2 text-sm min-w-0">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium truncate" title={item.job_name}>{item.job_name}</p>
        <span className="text-xs text-red-700 bg-red-100 rounded px-1.5 py-0.5">
          {failurePct}
        </span>
      </div>
      <p className="text-xs text-muted-foreground mt-1">
        {item.failed_executions}/{item.total_executions} failed
        {item.current_failure_streak > 0 ? ` · streak ${item.current_failure_streak}` : ""}
        {item.recurrence_count ? ` · recurrence ${item.recurrence_count}` : ""}
      </p>
      {item.last_failure_at && (
        <p className="text-xs text-muted-foreground mt-0.5">
          Last failure: {formatTimestamp(item.last_failure_at)}
        </p>
      )}
    </div>
  );
}

function JobsView({
  query,
  health,
}: {
  query: ReturnType<typeof useRuntimeJobs>;
  health: ReturnType<typeof useRuntimeJobHealth>;
}) {
  if (query.isLoading || health.isLoading) return <StatusBox>Loading runtime jobs…</StatusBox>;
  if (query.isError || health.isError) return <StatusBox error>Failed to load runtime jobs.</StatusBox>;
  const jobs = query.data?.jobs ?? [];
  const healthByJob = new Map((health.data?.health ?? []).map((item) => [item.job_id, item]));
  if (jobs.length === 0) {
    return <StatusBox>No runtime jobs found. Run the demo seed or connect a runtime source.</StatusBox>;
  }
  return (
    <div className="border rounded-lg overflow-hidden">
      <table className="w-full text-sm table-fixed">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-2 w-[24%]">Job</th>
            <th className="text-left font-medium px-3 py-2 w-[16%]">Engine</th>
            <th className="text-left font-medium px-3 py-2 w-[16%]">Environment</th>
            <th className="text-left font-medium px-3 py-2 w-[14%]">Health</th>
            <th className="text-left font-medium px-3 py-2 w-[18%]">Latest run</th>
            <th className="text-left font-medium px-3 py-2 w-[12%]">Owner</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <JobRow key={`${job.source_key}:${job.job_id}`} job={job} health={healthByJob.get(job.job_id)} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JobRow({ job, health }: { job: RuntimeJob; health: RuntimeJobHealth | undefined }) {
  const failurePct = health ? `${Math.round(health.failure_rate * 100)}%` : "—";
  return (
    <tr className="border-t">
      <td className="px-3 py-2 min-w-0">
        <p className="font-medium truncate" title={job.display_name ?? job.name}>
          {job.display_name ?? job.name}
        </p>
        <p className="text-xs text-muted-foreground truncate" title={job.job_id}>
          {job.source_key} · {job.job_id}
        </p>
      </td>
      <td className="px-3 py-2 truncate" title={job.engine_name ?? undefined}>
        {job.engine_name ?? "unknown"}
      </td>
      <td className="px-3 py-2 truncate" title={job.environment_name ?? undefined}>
        {job.environment_name ?? "unknown"}
      </td>
      <td className="px-3 py-2">
        <span className={cn("px-1.5 py-0.5 rounded text-xs", health && health.failed_runs > 0 ? "bg-red-100 text-red-700" : "bg-green-100 text-green-700")}>
          {failurePct} failed
        </span>
        {health && health.current_failure_streak > 0 && (
          <p className="text-xs text-muted-foreground mt-0.5">streak {health.current_failure_streak}</p>
        )}
      </td>
      <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
        {health?.latest_run_at ? formatTimestamp(health.latest_run_at) : "unknown"}
      </td>
      <td className="px-3 py-2 truncate" title={job.owner ?? undefined}>
        {job.owner ?? "—"}
      </td>
    </tr>
  );
}

function RunsView({
  query,
  selectedId,
  onSelect,
}: {
  query: ReturnType<typeof useRuntimeJobRuns>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (query.isLoading) return <StatusBox>Loading job runs…</StatusBox>;
  if (query.isError) return <StatusBox error>Failed to load job runs.</StatusBox>;
  const runs = query.data?.runs ?? [];
  if (runs.length === 0) {
    return <StatusBox>No runtime job runs found.</StatusBox>;
  }
  return (
    <div className="border rounded-lg overflow-hidden">
      <table className="w-full text-sm table-fixed">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-2 w-[22%]">Run</th>
            <th className="text-left font-medium px-3 py-2 w-[20%]">Job</th>
            <th className="text-left font-medium px-3 py-2 w-[16%]">Engine</th>
            <th className="text-left font-medium px-3 py-2 w-[10%]">State</th>
            <th className="text-left font-medium px-3 py-2 w-[20%]">Started</th>
            <th className="text-right font-medium px-3 py-2 w-[12%]">Duration</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const selectable = Boolean(run.execution_id);
            return (
              <tr
                key={`${run.source_key}:${run.run_id}`}
                onClick={() => run.execution_id && onSelect(run.execution_id)}
                className={cn(
                  "border-t transition-colors",
                  selectable && "cursor-pointer hover:bg-muted/40",
                  selectedId === run.execution_id && "bg-muted/60",
                )}
              >
                <td className="px-3 py-2 min-w-0">
                  <p className="font-mono text-xs truncate" title={run.run_id}>{run.run_id}</p>
                  {run.execution_id && (
                    <p className="text-xs text-muted-foreground truncate" title={run.execution_id}>
                      execution {run.execution_id}
                    </p>
                  )}
                </td>
                <td className="px-3 py-2 truncate" title={run.job_name}>{run.job_name}</td>
                <td className="px-3 py-2 truncate" title={run.engine_name ?? undefined}>
                  {run.engine_name ?? "unknown"}
                </td>
                <td className="px-3 py-2">
                  <StateBadge state={run.state} />
                </td>
                <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
                  {formatTimestamp(run.started_at)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {formatDuration(run.duration_ms)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PlansView({
  query,
  health,
}: {
  query: ReturnType<typeof useRuntimePlans>;
  health: ReturnType<typeof useRuntimePlanHealth>;
}) {
  if (query.isLoading || health.isLoading) return <StatusBox>Loading runtime plans…</StatusBox>;
  if (query.isError || health.isError) return <StatusBox error>Failed to load runtime plans.</StatusBox>;
  const plans = query.data?.plans ?? [];
  const healthByPlan = new Map((health.data?.health ?? []).map((item) => [item.plan_id, item]));
  if (plans.length === 0) {
    return <StatusBox>No runtime plans found. Run the demo seed or connect a runtime source.</StatusBox>;
  }
  return (
    <div className="border rounded-lg overflow-hidden">
      <table className="w-full text-sm table-fixed">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-2 w-[24%]">Plan</th>
            <th className="text-left font-medium px-3 py-2 w-[22%]">Steps</th>
            <th className="text-left font-medium px-3 py-2 w-[16%]">Workspace</th>
            <th className="text-left font-medium px-3 py-2 w-[14%]">Health</th>
            <th className="text-left font-medium px-3 py-2 w-[16%]">Latest run</th>
            <th className="text-left font-medium px-3 py-2 w-[8%]">Owner</th>
          </tr>
        </thead>
        <tbody>
          {plans.map((plan) => (
            <PlanRow key={`${plan.source_key}:${plan.plan_id}`} plan={plan} health={healthByPlan.get(plan.plan_id)} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PlanRow({ plan, health }: { plan: RuntimePlan; health: RuntimePlanHealth | undefined }) {
  const failurePct = health ? `${Math.round(health.failure_rate * 100)}%` : "—";
  const stepNames = plan.steps.map((step) => step.job_name ?? step.name).filter(Boolean).join(", ");
  return (
    <tr className="border-t">
      <td className="px-3 py-2 min-w-0">
        <p className="font-medium truncate" title={plan.display_name ?? plan.name}>
          {plan.display_name ?? plan.name}
        </p>
        <p className="text-xs text-muted-foreground truncate" title={plan.plan_id}>
          {plan.source_key} · {plan.plan_id}
        </p>
      </td>
      <td className="px-3 py-2 truncate" title={stepNames || undefined}>
        {plan.step_count ?? plan.steps.length} step{(plan.step_count ?? plan.steps.length) === 1 ? "" : "s"}
        {stepNames ? ` · ${stepNames}` : ""}
      </td>
      <td className="px-3 py-2 truncate" title={plan.workspace_name ?? undefined}>
        {plan.workspace_name ?? "unknown"}
      </td>
      <td className="px-3 py-2">
        <span className={cn("px-1.5 py-0.5 rounded text-xs", health && health.failed_runs > 0 ? "bg-red-100 text-red-700" : "bg-green-100 text-green-700")}>
          {failurePct} failed
        </span>
        {health && health.current_failure_streak > 0 && (
          <p className="text-xs text-muted-foreground mt-0.5">streak {health.current_failure_streak}</p>
        )}
      </td>
      <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
        {health?.latest_run_at ? formatTimestamp(health.latest_run_at) : "unknown"}
      </td>
      <td className="px-3 py-2 truncate" title={plan.owner ?? undefined}>
        {plan.owner ?? "—"}
      </td>
    </tr>
  );
}

function PlanRunsView({ query }: { query: ReturnType<typeof useRuntimePlanRuns> }) {
  if (query.isLoading) return <StatusBox>Loading plan runs…</StatusBox>;
  if (query.isError) return <StatusBox error>Failed to load plan runs.</StatusBox>;
  const runs = query.data?.runs ?? [];
  if (runs.length === 0) {
    return <StatusBox>No runtime plan runs found.</StatusBox>;
  }
  return (
    <div className="border rounded-lg overflow-hidden">
      <table className="w-full text-sm table-fixed">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-2 w-[24%]">Run</th>
            <th className="text-left font-medium px-3 py-2 w-[22%]">Plan</th>
            <th className="text-left font-medium px-3 py-2 w-[26%]">Step results</th>
            <th className="text-left font-medium px-3 py-2 w-[10%]">State</th>
            <th className="text-left font-medium px-3 py-2 w-[18%]">Started</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={`${run.source_key}:${run.run_id}`} className="border-t">
              <td className="px-3 py-2 font-mono text-xs truncate" title={run.run_id}>
                {run.run_id}
              </td>
              <td className="px-3 py-2 truncate" title={run.plan_name}>{run.plan_name}</td>
              <td className="px-3 py-2 truncate" title={formatStepResults(run)}>
                {formatStepResults(run)}
              </td>
              <td className="px-3 py-2">
                <StateBadge state={run.state} />
              </td>
              <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
                {formatTimestamp(run.started_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatStepResults(run: RuntimePlanRun): string {
  if (run.step_results.length === 0) return "No step results";
  return run.step_results
    .map((step) => `${step.job_name ?? step.step_id}: ${step.state ?? "unknown"}`)
    .join(", ");
}

function ExecutionsView({
  query,
  stateFilter,
  onStateFilter,
  jobName,
  onJobName,
  selectedId,
  onSelect,
}: {
  query: ReturnType<typeof useRuntimeExecutions>;
  stateFilter: "" | RuntimeExecutionState;
  onStateFilter: (v: "" | RuntimeExecutionState) => void;
  jobName: string;
  onJobName: (v: string) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={stateFilter}
          onChange={(e) => onStateFilter(e.target.value as "" | RuntimeExecutionState)}
          className="px-3 py-2 border rounded-md text-sm bg-background"
          aria-label="Filter by state"
        >
          {STATE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <input
          value={jobName}
          onChange={(e) => onJobName(e.target.value)}
          placeholder="Filter by component name"
          className="px-3 py-2 border rounded-md text-sm bg-background flex-1 min-w-[12rem]"
          aria-label="Filter by component name"
        />
      </div>

      {query.isLoading ? (
        <StatusBox>Loading executions…</StatusBox>
      ) : query.isError ? (
        <StatusBox error>Failed to load executions.</StatusBox>
      ) : !query.data || query.data.executions.length === 0 ? (
        <StatusBox>No executions found.</StatusBox>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-sm table-fixed">
            <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
              <tr>
                <th className="text-left font-medium px-3 py-2 w-[22%]">Execution</th>
                <th className="text-left font-medium px-3 py-2 w-[20%]">Component</th>
                <th className="text-left font-medium px-3 py-2 w-[16%]">Project</th>
                <th className="text-left font-medium px-3 py-2 w-[12%]">State</th>
                <th className="text-left font-medium px-3 py-2 w-[20%]">Started</th>
                <th className="text-right font-medium px-3 py-2 w-[10%]">Duration</th>
              </tr>
            </thead>
            <tbody>
              {query.data.executions.map((e) => (
                <tr
                  key={e.execution_id}
                  onClick={() => onSelect(e.execution_id)}
                  className={cn(
                    "border-t cursor-pointer hover:bg-muted/40 transition-colors",
                    selectedId === e.execution_id && "bg-muted/60",
                  )}
                >
                  <td className="px-3 py-2 font-mono text-xs truncate" title={e.execution_id}>
                    {e.execution_id}
                  </td>
                  <td className="px-3 py-2 truncate" title={e.job_name}>{e.job_name}</td>
                  <td className="px-3 py-2 truncate text-muted-foreground">
                    {e.project_name ?? "—"}
                  </td>
                  <td className="px-3 py-2">
                    <StateBadge state={e.state} />
                  </td>
                  <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
                    {formatTimestamp(e.started_at)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatDuration(e.duration_ms)}
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

function FailuresView({
  query,
  selectedId,
  onSelect,
}: {
  query: ReturnType<typeof useRuntimeFailures>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (query.isLoading) return <StatusBox>Loading failures…</StatusBox>;
  if (query.isError) return <StatusBox error>Failed to load failures.</StatusBox>;
  if (!query.data || query.data.failures.length === 0) {
    return <StatusBox>No failures recorded.</StatusBox>;
  }

  return (
    <div className="border rounded-lg overflow-hidden">
      <table className="w-full text-sm table-fixed">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-2 w-[18%]">Component</th>
            <th className="text-left font-medium px-3 py-2 w-[20%]">Failed at</th>
            <th className="text-left font-medium px-3 py-2 w-[42%]">Error summary</th>
            <th className="text-left font-medium px-3 py-2 w-[20%]">Execution</th>
          </tr>
        </thead>
        <tbody>
          {query.data.failures.map((f) => (
            <tr
              key={f.execution_id}
              onClick={() => onSelect(f.execution_id)}
              className={cn(
                "border-t cursor-pointer hover:bg-muted/40 transition-colors",
                selectedId === f.execution_id && "bg-muted/60",
              )}
            >
              <td className="px-3 py-2 truncate" title={f.job_name}>{f.job_name}</td>
              <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
                {formatTimestamp(f.failed_at)}
              </td>
              <td className="px-3 py-2">
                <span className="flex items-start gap-1.5">
                  <AlertTriangle className="h-3.5 w-3.5 text-red-500 mt-0.5 shrink-0" />
                  <span className="text-red-700">{f.error_summary}</span>
                </span>
              </td>
              <td className="px-3 py-2 font-mono text-xs truncate" title={f.execution_id}>
                {f.execution_id}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DiagnosisDetailPanel({
  executionId,
  detail,
  detailLoading,
  detailError,
  onClose,
}: {
  executionId: string;
  detail: RuntimeExecution | null;
  detailLoading: boolean;
  detailError: boolean;
  onClose: () => void;
}) {
  return (
    <div className="border rounded-lg bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-red-500" />
            <span className="font-mono text-xs truncate" title={executionId}>
              {executionId}
            </span>
          </h2>
          {detail && (
            <p className="text-xs text-muted-foreground mt-0.5">
              {detail.job_name}
              {detail.project_name ? ` · ${detail.project_name}` : ""} ·{" "}
              <span className="inline-flex align-middle">
                <StateBadge state={detail.state} />
              </span>{" "}
              · {formatTimestamp(detail.started_at)} · {formatDuration(detail.duration_ms)}
            </p>
          )}
          {detailLoading && <p className="text-xs text-muted-foreground mt-0.5">Loading detail…</p>}
          {detailError && (
            <p className="text-xs text-red-600 mt-0.5">Execution detail unavailable.</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onClose}
            className="px-2 py-1.5 border rounded-md text-xs hover:bg-muted transition-colors"
          >
            Close
          </button>
        </div>
      </div>

      <div className="px-4 py-3">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
          Failure Diagnosis
        </p>
        <RuntimeInsightPanel executionId={executionId} />
      </div>
    </div>
  );
}

function StatusBox({ children, error }: { children: React.ReactNode; error?: boolean }) {
  return (
    <div
      className={cn(
        "border rounded-lg px-4 py-8 text-center text-sm",
        error ? "text-red-600 border-red-200 bg-red-50" : "text-muted-foreground",
      )}
    >
      {children}
    </div>
  );
}
