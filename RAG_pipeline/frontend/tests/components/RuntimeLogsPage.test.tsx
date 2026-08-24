import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RuntimeLogsPage from "@/app/admin/logs/page";
import RuntimeExecutionsPage from "@/app/admin/logs/executions/page";
import RuntimePerformancePage from "@/app/admin/logs/performance/page";
import RuntimeSignaturesPage from "@/app/admin/logs/signatures/page";
import RuntimeIncidentsPage from "@/app/admin/logs/incidents/page";

// The whole hooks module is replaced, so every hook the console tree touches must
// be present here — including the ones the insight panel and log viewer use.
const mocks = vi.hoisted(() => ({
  useRuntimeExecution: vi.fn(), useRuntimeExecutions: vi.fn(), useRuntimeHealth: vi.fn(),
  useRuntimeIncidents: vi.fn(), useRuntimeIncident: vi.fn(), useRuntimeJobs: vi.fn(),
  useRuntimeOverview: vi.fn(), useRuntimePlans: vi.fn(), useRuntimeSources: vi.fn(),
  useRuntimeEngines: vi.fn(), useUserProjects: vi.fn(), handleSendMessage: vi.fn(), push: vi.fn(),
  useRuntimeTrend: vi.fn(), useRuntimeJobReliability: vi.fn(),
  useRuntimeComponentHotspots: vi.fn(), useRuntimeJobThroughput: vi.fn(),
  useRuntimeErrorSignatures: vi.fn(), useRuntimeExecutionLogs: vi.fn(),
  useRuntimeExecutionSummary: vi.fn(), useRuntimeExecutionTimeline: vi.fn(),
  useRuntimeIncidentEvidence: vi.fn(), useRuntimeSimilarFailures: vi.fn(),
}));

vi.mock("@/hooks/useRuntimeLogs", () => mocks);
vi.mock("@/hooks/useUserProjects", () => ({ useUserProjects: mocks.useUserProjects }));
vi.mock("@/contexts/ChatContext", () => ({ useChatContext: () => ({ handleSendMessage: mocks.handleSendMessage }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));

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
};

const RELIABILITY = {
  jobs: [
    { project_id: "werkorder", source_key: "tmc", job_name: "Maximo2RHM_Workorders", total_runs: 142, success_runs: 138, failed_runs: 4, unfinished_runs: 1, failure_rate: 0.0282, current_failure_streak: 2, first_run_at: "2026-08-01T00:00:00Z", last_run_at: "2026-08-16T22:00:00Z", latest_state: "failed", p50_duration_ms: 10600, p95_duration_ms: 25400 },
    { project_id: "werkorder", source_key: "tmc", job_name: "Quintiq2Maximo_PlannedWorkOrder", total_runs: 90, success_runs: 90, failed_runs: 0, unfinished_runs: 0, failure_rate: 0, current_failure_streak: 0, first_run_at: "2026-08-01T00:00:00Z", last_run_at: "2026-08-16T21:00:00Z", latest_state: "success", p50_duration_ms: 2700, p95_duration_ms: 6200 },
  ],
  total: 2,
};

function setDefaults() {
  mocks.useUserProjects.mockReturnValue(queryState({ projects: [] }));
  mocks.useRuntimeOverview.mockReturnValue(queryState({ overview: { engines_online: 2, engines_total: 3, engines_unhealthy: 0, jobs_unhealthy: 1, plans_unhealthy: 0 } }));
  mocks.useRuntimeExecutions.mockReturnValue(queryState({ executions: [{ execution_id: "exec-1", job_name: "orders_sync", state: "failed", started_at: "2026-07-15T10:00:00Z", ended_at: "2026-07-15T10:00:10Z", duration_ms: 1000 }], total: 1 }));
  mocks.useRuntimeHealth.mockReturnValue(queryState({ health: [{ job_name: "orders_sync", failure_rate: 1, current_failure_streak: 1 }], total: 1 }));
  mocks.useRuntimeIncidents.mockReturnValue(queryState({ incidents: [], total: 0 }));
  mocks.useRuntimeExecution.mockReturnValue(queryState({ execution: { execution_id: "exec-1", job_name: "orders_sync", state: "failed", started_at: "2026-07-15T10:00:00Z" } }));
  mocks.useRuntimeIncident.mockReturnValue(queryState({ incident: null }));
  mocks.useRuntimeJobs.mockReturnValue(queryState({ jobs: [], total: 0 }));
  mocks.useRuntimePlans.mockReturnValue(queryState({ plans: [], total: 0 }));
  mocks.useRuntimeSources.mockReturnValue(queryState({ sources: [], total: 0 }));
  mocks.useRuntimeEngines.mockReturnValue(queryState({ engines: [], total: 0 }));
  mocks.useRuntimeTrend.mockReturnValue(queryState(TREND));
  mocks.useRuntimeJobReliability.mockReturnValue(queryState(RELIABILITY));
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
      sources: [{ project_id: "cdc", source_key: "tmc", label: "vw_TMC", source_type: "talend_tmc", status: "ok", enabled: true, sync_interval_seconds: 300, last_synced_at: new Date(Date.now() - 60_000).toISOString() }],
      total: 1,
    }));
    renderPage(RuntimeLogsPage);
    expect(screen.getByText(/Live · imported 1m ago/)).toBeTruthy();
    expect(screen.queryByText(/Stale/)).toBeNull();
  });

  it("reports a stale feed when the newest import is older than three polling cycles", () => {
    mocks.useRuntimeSources.mockReturnValue(queryState({
      sources: [{ project_id: "cdc", source_key: "tmc", label: "vw_TMC", source_type: "talend_tmc", status: "ok", enabled: true, sync_interval_seconds: 300, last_synced_at: new Date(Date.now() - 3 * 3600_000).toISOString() }],
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
    await vi.waitFor(() => expect(mocks.handleSendMessage).toHaveBeenCalledWith({ query: "Why did orders fail?", project_id: undefined }));
    expect(mocks.push).toHaveBeenCalledWith("/");
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

  it("scopes every hub aggregate to the selected time window", () => {
    renderPage(RuntimeLogsPage);
    for (const hook of [mocks.useRuntimeTrend, mocks.useRuntimeJobReliability]) {
      const filters = hook.mock.calls[0][0] as { since?: string };
      expect(typeof filters.since).toBe("string");
    }
    selectOption("Time window", /24 hours/i);
    const trendCalls = mocks.useRuntimeTrend.mock.calls;
    const last = trendCalls[trendCalls.length - 1][0] as { bucket?: string };
    expect(last.bucket).toBe("hour");
  });

  it("ranks needs-attention by live failure signal and shows the last run", () => {
    renderPage(RuntimeLogsPage);
    expect(screen.getByText("Maximo2RHM_Workorders")).toBeTruthy();
    // A job with no failures and no unfinished runs is not an attention signal.
    expect(screen.queryByText("Quintiq2Maximo_PlannedWorkOrder")).toBeNull();
    expect(screen.getByText(/4 of 142 failed/)).toBeTruthy();
    expect(screen.getByText(/1 never finished/)).toBeTruthy();
  });

  it("surfaces the run-outcome and duration series with a legend and a table view", () => {
    renderPage(RuntimeLogsPage);
    expect(screen.getByText("Runs per day")).toBeTruthy();
    expect(screen.getByText("p95 duration")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "table" }));
    // "Failure rate" is also a KPI label, so assert on values unique to the table.
    expect(screen.getByText("44s")).toBeTruthy();
    expect(screen.getByText("6%")).toBeTruthy();
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
