import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RuntimeLogsPage from "@/app/admin/logs/page";
import RuntimeExecutionsPage from "@/app/admin/logs/executions/page";
import RuntimePerformancePage from "@/app/admin/logs/performance/page";
import RuntimeSignaturesPage from "@/app/admin/logs/signatures/page";
import RuntimeIncidentsPage from "@/app/admin/logs/incidents/page";
import RuntimeRoutesPage from "@/app/admin/logs/routes/page";
import RuntimeDataServicesPage from "@/app/admin/logs/data-services/page";
import {
  alignTrendPoints,
  buildDurationScale,
} from "@/components/runtime/RuntimeOutcomeTrend";

// The whole hooks module is replaced, so every hook the console tree touches must
// be present here — including the ones the insight panel and log viewer use.
const mocks = vi.hoisted(() => ({
  useRuntimeExecution: vi.fn(), useRuntimeExecutions: vi.fn(), useRuntimeHealth: vi.fn(),
  useRuntimeIncidents: vi.fn(), useRuntimeIncident: vi.fn(), useRuntimeJobs: vi.fn(),
  useRuntimeDeployments: vi.fn(),
  useRuntimeOverview: vi.fn(), useRuntimePlans: vi.fn(), useRuntimeSources: vi.fn(),
  useRuntimeEngines: vi.fn(), useUserProjects: vi.fn(), handleSendMessage: vi.fn(), push: vi.fn(), replace: vi.fn(),
  searchParams: vi.fn(),
  useRuntimeTrend: vi.fn(), useRuntimeJobReliability: vi.fn(), useRuntimeJobInsights: vi.fn(),
  useRuntimeComponentHotspots: vi.fn(), useRuntimeJobThroughput: vi.fn(),
  useRuntimeErrorSignatures: vi.fn(), useRuntimeExecutionLogs: vi.fn(),
  useRuntimeExecutionSummary: vi.fn(), useRuntimeExecutionTimeline: vi.fn(),
  useRuntimeIncidentEvidence: vi.fn(), useRuntimeSimilarFailures: vi.fn(),
}));

vi.mock("@/hooks/useRuntimeLogs", () => mocks);
vi.mock("@/hooks/useUserProjects", () => ({ useUserProjects: mocks.useUserProjects }));
vi.mock("@/contexts/ChatContext", () => ({ useChatContext: () => ({ handleSendMessage: mocks.handleSendMessage }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  usePathname: () => "/admin/logs",
  useSearchParams: () => new URLSearchParams(mocks.searchParams()),
}));

function queryState(data: unknown) {
  return { data, isLoading: false, isError: false, isSuccess: true, refetch: vi.fn() };
}

// The console drives its own auto-refresh through the query client, so every
// render needs a provider even though every data hook is mocked.
function renderPage(Page: React.ComponentType) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    React.createElement(QueryClientProvider, { client }, React.createElement(Page)),
  );
}

/** Pick an option from a SelectMenu (Radix opens on keyboard, not a click). */
function selectOption(triggerName: string, optionName: string | RegExp) {
  fireEvent.keyDown(screen.getByRole("button", { name: triggerName }), { key: "ArrowDown" });
  fireEvent.keyDown(screen.getByRole("menuitem", { name: optionName }), { key: "Enter" });
}

const TREND = {
  bucket: "day",
  points: [
    { bucket_start: "2026-08-14T00:00:00Z", total_runs: 70, success_runs: 70, failed_runs: 0, running_runs: 0, unknown_runs: 0, failure_rate: 0, p50_duration_ms: 9000, p95_duration_ms: 29800 },
    { bucket_start: "2026-08-15T00:00:00Z", total_runs: 72, success_runs: 68, failed_runs: 4, running_runs: 0, unknown_runs: 0, failure_rate: 0.0556, p50_duration_ms: 10600, p95_duration_ms: 43500 },
  ],
  total: 2,
  coverage: {
    observed_from: "2026-07-13T11:16:06Z",
    observed_until: "2026-08-15T23:59:00Z",
    completeness: "unknown",
  },
};

const RELIABILITY = {
  jobs: [
    { project_id: "werkorder", source_key: "tmc", job_name: "Maximo2RHM_Workorders", total_runs: 142, success_runs: 138, failed_runs: 4, unfinished_runs: 1, failure_rate: 0.0282, current_failure_streak: 2, first_run_at: "2026-08-01T00:00:00Z", last_run_at: "2026-08-16T22:00:00Z", latest_state: "failed", p50_duration_ms: 10600, p95_duration_ms: 25400 },
    { project_id: "werkorder", source_key: "tmc", job_name: "Quintiq2Maximo_PlannedWorkOrder", total_runs: 90, success_runs: 90, failed_runs: 0, unfinished_runs: 0, failure_rate: 0, current_failure_streak: 0, first_run_at: "2026-08-01T00:00:00Z", last_run_at: "2026-08-16T21:00:00Z", latest_state: "success", p50_duration_ms: 2700, p95_duration_ms: 6200 },
  ],
  total: 2,
};

const JOB_INSIGHTS = {
  insights: [
    {
      project_id: "werkorder",
      source_key: "tmc",
      job_name: "Maximo2RHM_Workorders",
      total_runs: 142,
      success_runs: 138,
      failed_runs: 4,
      unfinished_runs: 1,
      failure_rate: 0.0282,
      first_run_at: "2026-08-01T00:00:00Z",
      last_run_at: "2026-08-16T22:00:00Z",
      failures_with_logs: 3,
      diagnosed_failures: 2,
      capture_ceiling_reached: 1,
      evidence_completeness: "partial",
      pattern_id: "database.connection_reset",
      confidence: "medium",
      occurrences: 2,
      most_recent_at: "2026-08-16T22:00:00Z",
      failed_component: "tOracleConnection_1",
      termination_component: "tDie_1",
      causal_message: "IO Error: Connection reset",
      root_cause_candidate: "The database connection was reset before the job terminated.",
      cause_origin: "deterministic",
      next_checks: ["Check database connectivity from the runtime engine."],
    },
  ],
  total: 1,
};

function setDefaults() {
  mocks.searchParams.mockReturnValue("");
  mocks.useUserProjects.mockReturnValue(queryState({
    projects: [{ id: "werkorder", display_name: "Werkorder" }],
  }));
  mocks.useRuntimeOverview.mockReturnValue(queryState({ overview: { engines_online: 2, engines_total: 3, engines_unhealthy: 0, jobs_unhealthy: 1, plans_unhealthy: 0 } }));
  mocks.useRuntimeExecutions.mockReturnValue(queryState({ executions: [{ execution_id: "exec-1", job_name: "orders_sync", state: "failed", started_at: "2026-07-15T10:00:00Z", ended_at: "2026-07-15T10:00:10Z", duration_ms: 1000 }], total: 1 }));
  mocks.useRuntimeHealth.mockReturnValue(queryState({ health: [{ job_name: "orders_sync", failure_rate: 1, current_failure_streak: 1 }], total: 1 }));
  mocks.useRuntimeIncidents.mockReturnValue(queryState({ incidents: [], total: 0 }));
  mocks.useRuntimeExecution.mockReturnValue(queryState({ execution: { execution_id: "exec-1", job_name: "orders_sync", state: "failed", started_at: "2026-07-15T10:00:00Z" } }));
  mocks.useRuntimeIncident.mockReturnValue(queryState({ incident: null }));
  mocks.useRuntimeJobs.mockReturnValue(queryState({ jobs: [], total: 0 }));
  mocks.useRuntimeDeployments.mockReturnValue(queryState({ deployments: [], total: 0 }));
  mocks.useRuntimePlans.mockReturnValue(queryState({ plans: [], total: 0 }));
  mocks.useRuntimeSources.mockReturnValue(queryState({
    sources: [{ project_id: "werkorder", source_key: "tmc", label: "TMC", source_type: "tmc", status: "ok", enabled: true, sync_interval_seconds: 300, last_synced_at: null }],
    total: 1,
  }));
  mocks.useRuntimeEngines.mockReturnValue(queryState({ engines: [], total: 0 }));
  mocks.useRuntimeTrend.mockReturnValue(queryState(TREND));
  mocks.useRuntimeJobReliability.mockImplementation((filters: { attentionOnly?: boolean } = {}) =>
    queryState(filters.attentionOnly
      ? { jobs: [RELIABILITY.jobs[0]], total: 1 }
      : RELIABILITY),
  );
  mocks.useRuntimeJobInsights.mockReturnValue(queryState(JOB_INSIGHTS));
  mocks.useRuntimeComponentHotspots.mockReturnValue(queryState({ components: [], total: 0 }));
  mocks.useRuntimeJobThroughput.mockReturnValue(queryState({ jobs: [], total: 0 }));
  mocks.useRuntimeErrorSignatures.mockReturnValue(queryState({ signatures: [], total: 0 }));
  mocks.useRuntimeExecutionLogs.mockReturnValue(queryState({ execution_id: "exec-1", events: [], total: 0 }));
  mocks.useRuntimeExecutionSummary.mockReturnValue({ data: undefined, isLoading: false, isError: false, isSuccess: false, refetch: vi.fn() });
  mocks.useRuntimeExecutionTimeline.mockReturnValue(queryState(undefined));
  mocks.useRuntimeIncidentEvidence.mockReturnValue(queryState(undefined));
  mocks.useRuntimeSimilarFailures.mockReturnValue(queryState(undefined));
}

describe("RuntimeLogsPage", () => {
  beforeEach(() => { vi.clearAllMocks(); setDefaults(); });

  it("renders Routes and Data services as deployment views", () => {
    mocks.useRuntimeDeployments.mockReturnValue(queryState({
      deployments: [{
        project_id: "werkorder",
        source_key: "tmc",
        deployment_id: "route-run-1",
        executable_id: "route-1",
        executable_name: "Orders API Route",
        executable_kind: "route",
        state: "running",
        environment_name: "PROD",
        engine_name: "runtime-1",
        started_at: "2026-08-27T10:00:00Z",
        total_exchanges: 42,
        completed_exchanges: 41,
        failed_exchanges: 1,
      }, {
        project_id: "werkorder",
        source_key: "tmc",
        executable_id: "route-2",
        executable_name: "Customer API Route",
        executable_kind: "route",
        metric_path: "customer_route",
      }],
      total: 2,
    }));
    const routeView = renderPage(RuntimeRoutesPage);
    expect(screen.getByText("Route deployments")).toBeTruthy();
    expect(screen.getByText("Orders API Route")).toBeTruthy();
    expect(screen.getByText(/41 completed · 1 failed · 42 total/)).toBeTruthy();
    expect(screen.getByText("Customer API Route")).toBeTruthy();
    expect(screen.getByText("never deployed")).toBeTruthy();
    fireEvent.click(screen.getByText("Orders API Route"));
    expect(screen.getByRole("dialog", { name: "Runtime details" })).toBeTruthy();
    expect(mocks.useRuntimeExecutionLogs).toHaveBeenCalledWith(
      "route-run-1",
      expect.objectContaining({ limit: 100 }),
    );
    routeView.unmount();

    renderPage(RuntimeDataServicesPage);
    expect(screen.getByText("Data service deployments")).toBeTruthy();
  });

  it("renders a summary-first hub and does not invoke entity inventory queries", () => {
    renderPage(RuntimeLogsPage);
    expect(screen.getByRole("heading", { level: 1, name: "Runtime logs" })).toBeTruthy();
    expect(screen.getByText("Outcome trend")).toBeTruthy();
    expect(screen.getByText("Needs attention")).toBeTruthy();
    expect(mocks.useRuntimeJobs).not.toHaveBeenCalled();
    expect(mocks.useRuntimePlans).not.toHaveBeenCalled();
  });

  // --- live feed -----------------------------------------------------------
  // The console shows relative timestamps and auto-refreshes, so it must be
  // explicit about whether data is still arriving. A pulsing "Live" pill over a
  // three-hour-old import would be a lie.

  it("reports a live feed when the newest import is within the polling window", () => {
    mocks.useRuntimeSources.mockReturnValue(queryState({
      sources: [{ project_id: "werkorder", source_key: "tmc", label: "vw_TMC", source_type: "talend_tmc", status: "ok", enabled: true, sync_interval_seconds: 300, last_synced_at: new Date(Date.now() - 60_000).toISOString() }],
      total: 1,
    }));
    renderPage(RuntimeLogsPage);
    expect(screen.getByText(/Live · imported 1m ago/)).toBeTruthy();
    expect(screen.queryByText(/Stale/)).toBeNull();
  });

  it("reports a stale feed when the newest import is older than three polling cycles", () => {
    mocks.useRuntimeSources.mockReturnValue(queryState({
      sources: [{ project_id: "werkorder", source_key: "tmc", label: "vw_TMC", source_type: "talend_tmc", status: "ok", enabled: true, sync_interval_seconds: 300, last_synced_at: new Date(Date.now() - 3 * 3600_000).toISOString() }],
      total: 1,
    }));
    renderPage(RuntimeLogsPage);
    expect(screen.getByText(/Stale · 3h old/)).toBeTruthy();
    // The banner and the header pill derive from one computation, so they agree.
    expect(screen.getByText(/Last import 3h ago — data may be stale/)).toBeTruthy();
  });

  it("pauses and resumes the automatic refresh", () => {
    renderPage(RuntimeLogsPage);
    const toggle = screen.getByRole("button", { name: "Pause" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Resume" })).toHaveAttribute("aria-pressed", "false");
  });

  it("opens an accessible execution drawer from a run row", () => {
    renderPage(RuntimeLogsPage);
    fireEvent.click(screen.getAllByText("orders_sync")[0]);
    expect(screen.getByRole("dialog", { name: "Runtime details" })).toBeTruthy();
    expect(screen.getAllByText("exec-1").length).toBeGreaterThan(0);
  });

  it("sends the scoped question through the shared chat context", async () => {
    mocks.handleSendMessage.mockResolvedValue(undefined);
    renderPage(RuntimeLogsPage);
    fireEvent.change(screen.getByPlaceholderText("What changed in the failed runs?"), { target: { value: "Why did orders fail?" } });
    fireEvent.click(screen.getByText("Ask Nexus"));
    await vi.waitFor(() => expect(mocks.handleSendMessage).toHaveBeenCalledWith({ query: "Why did orders fail?", project_id: "werkorder" }));
    expect(mocks.push).toHaveBeenCalledWith("/");
  });

  it("keeps the selected project in every runtime tab link", () => {
    mocks.useUserProjects.mockReturnValue(queryState({
      projects: [
        { id: "werkorder", display_name: "Werkorder" },
        { id: "nexus_logs", display_name: "Nexus logs" },
        { id: "docs_only", display_name: "Docs only" },
      ],
    }));
    mocks.useRuntimeSources.mockReturnValue(queryState({
      sources: [
        { project_id: "werkorder", source_key: "tmc", label: "TMC", source_type: "talend_tmc" },
        { project_id: "nexus_logs", source_key: "tmc-123", label: "TMC", source_type: "tmc" },
        { project_id: "docs_only", source_key: "mock", label: "Mock", source_type: "mock" },
      ],
      total: 3,
    }));
    renderPage(RuntimeLogsPage);
    expect(screen.queryByText("All permitted projects")).toBeNull();
    expect(screen.queryByText("Docs only")).toBeNull();
    selectOption("Project scope", "Nexus logs");
    expect(screen.getByRole("link", { name: "Signatures" })).toHaveAttribute(
      "href",
      "/admin/logs/signatures?project=nexus_logs&window=30d",
    );
    expect(screen.getByRole("link", { name: "Estate" })).toHaveAttribute(
      "href",
      "/admin/logs/estate?project=nexus_logs&window=30d",
    );
  });

  it("keeps the selected time window in the URL and every runtime tab link", () => {
    renderPage(RuntimeLogsPage);
    selectOption("Time window", /Last 7 days/i);

    expect(mocks.replace).toHaveBeenLastCalledWith(
      "/admin/logs?project=werkorder&window=7d",
    );
    expect(screen.getByRole("link", { name: "Incidents" })).toHaveAttribute(
      "href",
      "/admin/logs/incidents?project=werkorder&window=7d",
    );
    expect(screen.getByRole("link", { name: "Jobs" })).toHaveAttribute(
      "href",
      "/admin/logs/jobs?project=werkorder&window=7d",
    );
  });

  it("restores the selected time window from the URL after changing routes", () => {
    mocks.searchParams.mockReturnValue("project=werkorder&window=7d");
    renderPage(RuntimeIncidentsPage);
    expect(screen.getByRole("button", { name: "Time window" })).toHaveTextContent("Last 7 days");
  });

  it("uses execution-only queries on the runs route and exposes the honest server total", () => {
    renderPage(RuntimeExecutionsPage);
    expect(screen.getByText("Showing 1-1 of 1 records")).toBeTruthy();
    expect(mocks.useRuntimeOverview).not.toHaveBeenCalled();
    expect(mocks.useRuntimeExecutions).toHaveBeenCalled();
  });

  // --- summary layer is driven by real aggregates, not by query page length ---

  it("derives KPI values from the window aggregate rather than the row count", () => {
    renderPage(RuntimeLogsPage);
    // 70 + 72 runs in the trend, of which 4 failed => 3%, not the 12-row page size.
    expect(screen.getByText("142")).toBeTruthy();
    expect(screen.getByText("3%")).toBeTruthy();
    expect(screen.getByText("4 failed")).toBeTruthy();
  });

  it("scopes history aggregates to the selected time window, while attention is current", () => {
    renderPage(RuntimeLogsPage);
    for (const hook of [mocks.useRuntimeTrend, mocks.useRuntimeJobReliability]) {
      const filters = hook.mock.calls[0][0] as { since?: string };
      expect(typeof filters.since).toBe("string");
    }
    const attentionFilters = mocks.useRuntimeJobReliability.mock.calls[1][0] as {
      since?: string;
      attentionOnly?: boolean;
    };
    expect(attentionFilters.since).toBeUndefined();
    expect(attentionFilters.attentionOnly).toBe(true);
    selectOption("Time window", /24 hours/i);
    const trendCalls = mocks.useRuntimeTrend.mock.calls;
    const last = trendCalls[trendCalls.length - 1][0] as { bucket?: string };
    expect(last.bucket).toBe("hour");
  });

  it("shows only unresolved latest-run signals, rather than historical failure totals", () => {
    renderPage(RuntimeLogsPage);
    expect(screen.getByText("Maximo2RHM_Workorders")).toBeTruthy();
    // A job with no failures and no unfinished runs is not an attention signal.
    expect(screen.queryByText("Quintiq2Maximo_PlannedWorkOrder")).toBeNull();
    expect(screen.getByText(/Latest run failed/)).toBeTruthy();
    expect(screen.getByText(/2 failures in a row/)).toBeTruthy();
  });

  it("explains the dominant issue and keeps origin separate from termination", () => {
    renderPage(RuntimeLogsPage);
    expect(screen.getByText(/database · connection reset/)).toBeTruthy();
    expect(screen.getByText(/Origin: tOracleConnection_1/)).toBeTruthy();
    expect(screen.getByText(/Stopped by: tDie_1/)).toBeTruthy();
    expect(screen.getByText("Observed:")).toBeTruthy();
    expect(screen.getByText("Interpretation:")).toBeTruthy();
    expect(screen.getByText("medium")).toBeTruthy();
    expect(screen.getByText(/Logs 3\/4 · Diagnosed 2\/4/)).toBeTruthy();
  });

  it("shows evidence coverage without guessing causes for unexplained failures", () => {
    renderPage(RuntimeLogsPage);
    expect(screen.getByText("Evidence quality")).toBeTruthy();
    expect(screen.getByText("3/4")).toBeTruthy();
    expect(screen.getByText("2/4")).toBeTruthy();
    expect(screen.getByText("2", { selector: "dd" })).toBeTruthy();
    expect(screen.getByText("1 failure has no logs")).toBeTruthy();
    expect(screen.getByText("not assigned a guessed cause")).toBeTruthy();
  });

  it("keeps a healthy source calm when there are no failures to explain", () => {
    mocks.useRuntimeJobReliability.mockReturnValue(queryState({
      jobs: [RELIABILITY.jobs[1]],
      total: 1,
    }));
    mocks.useRuntimeJobInsights.mockReturnValue(queryState({
      insights: [{
        ...JOB_INSIGHTS.insights[0],
        job_name: "Quintiq2Maximo_PlannedWorkOrder",
        total_runs: 90,
        success_runs: 90,
        failed_runs: 0,
        unfinished_runs: 0,
        failure_rate: 0,
        failures_with_logs: 0,
        diagnosed_failures: 0,
        capture_ceiling_reached: 0,
        evidence_completeness: "no_failures",
        pattern_id: null,
        confidence: null,
        occurrences: 0,
        failed_component: null,
        termination_component: null,
        causal_message: null,
        root_cause_candidate: null,
        cause_origin: null,
        next_checks: [],
      }],
      total: 1,
    }));
    renderPage(RuntimeLogsPage);
    expect(screen.getByText("No failures in this window, so there is nothing to diagnose.")).toBeTruthy();
    expect(screen.getByText("No active attention signals.")).toBeTruthy();
    expect(screen.queryByText("No useful diagnosis yet.")).toBeNull();
  });

  it("surfaces the run-outcome and duration series with a legend and a table view", () => {
    renderPage(RuntimeLogsPage);
    expect(screen.getByText("Captured runs")).toBeTruthy();
    expect(screen.getAllByText("Failure rate").length).toBeGreaterThan(0);
    expect(screen.getByText("p95 duration")).toBeTruthy();
    expect(screen.getByText("2 buckets")).toBeTruthy();
    expect(screen.getByText("72 runs")).toBeTruthy();
    expect(screen.getByText(/Captured history starts 13 Jul 2026/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "table" }));
    expect(screen.getAllByText("No captured records").length).toBeGreaterThan(0);
    expect(screen.getAllByText("44s")).toHaveLength(2);
    expect(screen.getAllByText("5.56%").length).toBeGreaterThan(0);
  });

  it("aligns trend buckets to the selected range from oldest to newest", () => {
    const aligned = alignTrendPoints(
      [...TREND.points].reverse(),
      "day",
      "2026-08-13T12:00:00Z",
      "2026-08-15T12:00:00Z",
    );

    expect(aligned.map((point) => point.bucket_start.slice(0, 10))).toEqual([
      "2026-08-13",
      "2026-08-14",
      "2026-08-15",
    ]);
    expect(aligned.map((point) => point.total_runs)).toEqual([0, 70, 72]);
    expect(aligned.map((point) => point.has_captured_records)).toEqual([false, true, true]);
    expect(aligned.map((point) => point.volume_plot)).toEqual([null, 70, 72]);
    expect(aligned.map((point) => point.is_partial)).toEqual([false, false, true]);
  });

  it("uses an explicit logarithmic duration scale when one outlier would flatten normal runs", () => {
    const scale = buildDurationScale([
      { ...TREND.points[0], p50_duration_ms: 5_000, p95_duration_ms: 20_000 },
      { ...TREND.points[1], p50_duration_ms: 6_000, p95_duration_ms: 2_203_200_000 },
    ]);

    expect(scale.mode).toBe("log");
    expect(scale.plot(20_000)).toBeLessThan(scale.plot(2_203_200_000)!);
    expect(scale.formatTick(scale.plot(2_203_200_000)!)).toBe("25.5d");
  });

  it("keeps a linear duration scale when measurements share a readable range", () => {
    const scale = buildDurationScale(TREND.points);

    expect(scale.mode).toBe("linear");
    expect(scale.plot(43_500)).toBe(43_500);
  });

  it("does not select log scale by comparing unlike percentile series", () => {
    const scale = buildDurationScale([
      { ...TREND.points[0], p50_duration_ms: 2_319, p95_duration_ms: 60_933 },
      { ...TREND.points[1], p50_duration_ms: 6_925, p95_duration_ms: 358_503 },
    ]);

    expect(scale.mode).toBe("linear");
  });

  it("surfaces the precise peak bucket failure rate separately from volume", () => {
    mocks.useRuntimeTrend.mockReturnValue(queryState({
      ...TREND,
      points: [{
        ...TREND.points[0],
        bucket_start: "2026-07-30T00:00:00Z",
        total_runs: 62,
        success_runs: 59,
        failed_runs: 3,
        failure_rate: 3 / 62,
      }],
      total: 1,
    }));

    renderPage(RuntimeLogsPage);
    expect(screen.getAllByText("4.84%").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/3 of 62/).length).toBeGreaterThan(0);
  });

  // --- drill-down now shows logs, insights and structured fields ---

  it("renders captured log lines in the execution drawer", () => {
    mocks.useRuntimeExecutionLogs.mockReturnValue(queryState({
      execution_id: "exec-1",
      events: [
        { execution_id: "exec-1", line_ordinal: 0, timestamp: "2026-07-15T10:00:01Z", severity: "info", message: "job is starting" },
        { execution_id: "exec-1", line_ordinal: 1, timestamp: "2026-07-15T10:00:05Z", severity: "error", message: "tMSSqlOutput_5 Data truncation" },
      ],
      total: 2,
    }));
    renderPage(RuntimeLogsPage);
    fireEvent.click(screen.getAllByText("orders_sync")[0]);
    const heading = screen.getByRole("heading", { name: /Log lines/ });
    expect(heading).toBeTruthy();
    expect(screen.getByText("tMSSqlOutput_5 Data truncation")).toBeTruthy();
    // The captured-line count is a chip on the heading.
    expect(within(heading).getByText("2")).toBeTruthy();
  });

  it("keeps repeated identical log lines distinct", () => {
    const repeated = { execution_id: "exec-1", timestamp: "2026-07-15T10:00:01Z", severity: "warning", message: "Failed request" };
    mocks.useRuntimeExecutionLogs.mockReturnValue(queryState({
      execution_id: "exec-1",
      events: [{ ...repeated, line_ordinal: 0 }, { ...repeated, line_ordinal: 1 }],
      total: 2,
    }));
    renderPage(RuntimeLogsPage);
    fireEvent.click(screen.getAllByText("orders_sync")[0]);
    expect(screen.getAllByText("Failed request")).toHaveLength(2);
  });

  it("renders structured incident fields that the scalar-only drawer dropped", () => {
    mocks.useRuntimeIncidents.mockReturnValue(queryState({
      incidents: [{
        project_id: "werkorder", incident_id: "inc-1", execution_id: "exec-9",
        job_name: "Maximo2RHM_Workorders", symptom_summary: "Execution failed",
        root_cause_candidate: "Target column rejected the value", confidence: "medium",
        next_checks: ["Check the target column width", "Re-run after widening the column"],
        estate_context: {},
      }],
      total: 1,
    }));
    renderPage(RuntimeIncidentsPage);
    fireEvent.click(screen.getByText("Target column rejected the value"));
    expect(screen.getByText("next checks")).toBeTruthy();
    expect(screen.getByText("Check the target column width")).toBeTruthy();
    expect(screen.getByText("Re-run after widening the column")).toBeTruthy();
  });

  it("labels a low-confidence cause as not a diagnosis", () => {
    mocks.useRuntimeIncidents.mockReturnValue(queryState({
      incidents: [{
        project_id: "werkorder", incident_id: "inc-2", execution_id: "exec-8",
        job_name: "RHM2Smartflow_Workorders",
        root_cause_candidate: "Execution terminated with errors; review log events for details.",
        confidence: "low", next_checks: [], estate_context: {},
      }],
      total: 1,
    }));
    renderPage(RuntimeIncidentsPage);
    expect(screen.getByText("low — not a diagnosis")).toBeTruthy();
  });

  // --- cause provenance -----------------------------------------------------
  // The common shape in real data is: no causal message, low confidence,
  // deterministic origin. That case must read as deliberate, not broken.

  it("renders the majority case — low confidence, rule engine, no causal message", () => {
    mocks.useRuntimeIncidents.mockReturnValue(queryState({
      incidents: [{
        project_id: "werkorder", incident_id: "inc-maj", execution_id: "exec-maj",
        job_name: "RHM2Smartflow_Workorders",
        root_cause_candidate: "Execution terminated with errors; review log events for details.",
        confidence: "low", cause_origin: "deterministic", pattern_id: "generic.unknown",
        causal_message: null, next_checks: [], estate_context: {},
      }],
      total: 1,
    }));
    renderPage(RuntimeIncidentsPage);
    expect(screen.getByText("Execution terminated with errors; review log events for details.")).toBeTruthy();
    expect(screen.getByText("low — not a diagnosis")).toBeTruthy();
    expect(screen.getByText("rule engine")).toBeTruthy();
    // A contentless catalog tier is not worth naming to the reader.
    expect(screen.queryByText("generic.unknown")).toBeNull();
  });

  it("marks an LLM-inferred cause unmistakably", () => {
    mocks.useRuntimeIncidents.mockReturnValue(queryState({
      incidents: [{
        project_id: "werkorder", incident_id: "inc-llm", execution_id: "exec-llm",
        job_name: "Maximo2RHM_Workorders", root_cause_candidate: "Probable upstream schema drift",
        confidence: "low", cause_origin: "llm_fallback", pattern_id: "generic.unknown",
        next_checks: [], estate_context: {},
      }],
      total: 1,
    }));
    renderPage(RuntimeIncidentsPage);
    expect(screen.getByText("Inferred by LLM")).toBeTruthy();
    expect(screen.queryByText("rule engine")).toBeNull();
  });

  it("names the catalog rule in the drawer when it identifies a real platform fault", () => {
    const incident = {
      project_id: "werkorder", incident_id: "inc-trunc", execution_id: "exec-trunc",
      job_name: "Maximo2RHM_Workorders",
      root_cause_candidate: "Target column too narrow for the incoming value: SQL Server rejected the batch with a data-truncation error (BatchUpdateException).",
      causal_message: "Data truncation", confidence: "medium",
      cause_origin: "deterministic", pattern_id: "sqlserver.truncation",
      next_checks: ["Widen the target column"], estate_context: {},
    };
    mocks.useRuntimeIncidents.mockReturnValue(queryState({ incidents: [incident], total: 1 }));
    mocks.useRuntimeIncident.mockReturnValue(queryState({ incident }));
    mocks.useRuntimeExecutionSummary.mockReturnValue(queryState({
      summary: {
        project_id: "werkorder", execution_id: "exec-trunc", job_name: "Maximo2RHM_Workorders",
        warning_count: 0, error_count: 1, has_incident: true, incident_id: "inc-trunc",
        evidence_refs: [], estate_context: {},
      },
    }));
    renderPage(RuntimeIncidentsPage);
    fireEvent.click(screen.getByText("Data truncation"));
    // Also present in the drawer's raw scalar dump, so match all occurrences.
    expect(screen.getAllByText("sqlserver.truncation").length).toBeGreaterThan(0);
    expect(screen.getAllByText("rule engine").length).toBeGreaterThan(0);
  });

  it("prefers the log's own diagnostic sentence over the composed template", () => {
    mocks.useRuntimeIncidents.mockReturnValue(queryState({
      incidents: [{
        project_id: "cdc", incident_id: "inc-3", execution_id: "exec-7", job_name: "bq_load",
        root_cause_candidate: "Execution terminated with MySQL error 200.",
        causal_message: 'tBigQueryOutput_2_tBQBE Invalid dataset ID "test_ zaf_del_clean".',
        confidence: "medium", next_checks: [], estate_context: {},
      }],
      total: 1,
    }));
    renderPage(RuntimeIncidentsPage);
    expect(screen.getByText('tBigQueryOutput_2_tBQBE Invalid dataset ID "test_ zaf_del_clean".')).toBeTruthy();
    expect(screen.queryByText("Execution terminated with MySQL error 200.")).toBeNull();
  });

  // --- runs route filters ---

  it("passes the stuck-run filters through to the executions query", () => {
    renderPage(RuntimeExecutionsPage);
    selectOption("Outcome", "Failed");
    selectOption("Minimum duration", "Longer than 24h");
    fireEvent.click(screen.getByLabelText("Never finished only"));
    const calls = mocks.useRuntimeExecutions.mock.calls;
    const last = calls[calls.length - 1][0] as Record<string, unknown>;
    expect(last.state).toBe("failed");
    expect(last.minDurationMs).toBe(86_400_000);
    expect(last.unfinished).toBe(true);
  });

  it("reports a run with no end time instead of calling it finished", () => {
    mocks.useRuntimeExecutions.mockReturnValue(queryState({
      executions: [{ execution_id: "exec-stuck", job_name: "maximoFourpsWorkOrder", state: "success", started_at: "2026-07-03T10:00:00Z", ended_at: null, duration_ms: null }],
      total: 1,
    }));
    renderPage(RuntimeExecutionsPage);
    expect(screen.getByText("never finished")).toBeTruthy();
  });

  // --- the previously unreachable rollups have routes ---

  it("renders component hotspots and throughput on the performance route", () => {
    mocks.useRuntimeComponentHotspots.mockReturnValue(queryState({
      components: [{ project_id: "alitalend", source_key: "tmc", component_key: "tHashInput_1", component_type: "tHashInput", source_connector_type: "tHashInput", target_connector_type: "tMap", executions: 591, rows_total: 2_962_701, rows_rejected: null, avg_duration_ms: 6263, p95_duration_ms: 14000 }],
      total: 1,
    }));
    renderPage(RuntimePerformancePage);
    expect(screen.getByText("Component hotspots")).toBeTruthy();
    expect(screen.getByText("Job throughput")).toBeTruthy();
    expect(screen.getByText("tHashInput_1")).toBeTruthy();
    expect(screen.getByText("tHashInput → tMap")).toBeTruthy();
    expect(screen.getByText("3M")).toBeTruthy();
  });

  it("renders recurring error signatures on the signatures route", () => {
    mocks.useRuntimeErrorSignatures.mockReturnValue(queryState({
      signatures: [{ project_id: "werkorder", signature_hash: "abc123def456", error_code: null, exception_class: "BatchUpdateException", component_name: "tMSSqlOutput_5", summary_text: "BatchUpdateException / tMSSqlOutput_5", occurrence_count: 16, first_seen_at: "2026-07-27T00:00:00Z", last_seen_at: "2026-07-27T23:00:00Z" }],
      total: 1,
    }));
    renderPage(RuntimeSignaturesPage);
    expect(screen.getByText("Error signatures")).toBeTruthy();
    expect(screen.getByText("BatchUpdateException / tMSSqlOutput_5")).toBeTruthy();
    expect(screen.getByText("16")).toBeTruthy();
    expect(mocks.useRuntimeComponentHotspots).not.toHaveBeenCalled();
  });
});
