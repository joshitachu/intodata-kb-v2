import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useRuntimeExecutions,
  useRuntimeExecutionLogs,
  useRuntimeExecutionSummary,
  useRuntimeExecutionTimeline,
  useRuntimeArtifacts,
  useRuntimeConnections,
  useRuntimeDependencies,
  useRuntimeDependencySummary,
  useRuntimeEngineHealth,
  useRuntimeEngines,
  useRuntimeHealth,
  useRuntimeJobHealth,
  useRuntimeJobRuns,
  useRuntimeJobs,
  useRuntimeIncident,
  useRuntimeIncidentEvidence,
  useRuntimeFailures,
  useRuntimeOverview,
  useRuntimePlanHealth,
  useRuntimePlanRuns,
  useRuntimePlans,
  useRuntimeSources,
  useRuntimeSimilarFailures,
  useRuntimeTrend,
  useRuntimeJobReliability,
  useRuntimeComponentHotspots,
  useRuntimeJobThroughput,
  useRuntimeErrorSignatures,
} from "@/hooks/useRuntimeLogs";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockFetchOnce(body: unknown, ok = true, status = 200) {
  return vi.spyOn(global, "fetch").mockResolvedValueOnce({
    ok,
    status,
    json: async () => body,
  } as Response);
}

describe("useRuntimeLogs", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("builds a query string from defined filters only", async () => {
    const fetchSpy = mockFetchOnce({ executions: [], total: 0 });

    const { result } = renderHook(
      () => useRuntimeExecutions({ state: "failed", jobName: "", limit: 50 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/executions?");
    expect(url).toContain("state=failed");
    expect(url).toContain("limit=50");
    // Empty jobName must be omitted entirely.
    expect(url).not.toContain("job_name");
  });

  it("parses a valid executions response", async () => {
    mockFetchOnce({
      executions: [
        {
          execution_id: "e1",
          job_name: "sync",
          project_name: "proj",
          state: "success",
          started_at: "2026-06-16T00:00:00Z",
          ended_at: null,
          duration_ms: 1200,
        },
      ],
      total: 1,
    });

    const { result } = renderHook(() => useRuntimeExecutions(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.total).toBe(1);
    expect(result.current.data?.executions[0].state).toBe("success");
  });

  it("surfaces an error when the response is not ok", async () => {
    mockFetchOnce({}, false, 401);

    const { result } = renderHook(() => useRuntimeFailures(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it("fetches runtime health with scoped filters", async () => {
    const fetchSpy = mockFetchOnce({
      health: [
        {
          project_id: "project-a",
          source_key: "mock",
          job_name: "orders",
          total_executions: 2,
          failed_executions: 1,
          failure_rate: 0.5,
          current_failure_streak: 1,
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeHealth({ projectId: "project-a", sourceKey: "mock", limit: 5 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/health?");
    expect(url).toContain("project_id=project-a");
    expect(url).toContain("source_key=mock");
    expect(result.current.data?.health[0].failure_rate).toBe(0.5);
  });

  it("fetches runtime sources with scoped filters", async () => {
    const fetchSpy = mockFetchOnce({
      sources: [
        {
          project_id: "project-a",
          source_key: "mock",
          source_type: "mock",
          label: "Demo runtime source",
          status: "success",
          is_demo: true,
          last_synced_at: "2026-07-02T10:00:00Z",
          last_seen_at: "2026-07-02T10:00:00Z",
          ingested_at: "2026-07-02T10:00:00Z",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeSources({ projectId: "project-a", sourceKey: "mock" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/sources?");
    expect(url).toContain("project_id=project-a");
    expect(result.current.data?.sources[0].is_demo).toBe(true);
  });

  it("fetches runtime engines", async () => {
    const fetchSpy = mockFetchOnce({
      engines: [
        {
          engine_id: "engine-prod-01",
          source_key: "mock",
          name: "remote-engine-prod-01",
          status: "online",
          version: "8.0.1",
          environment_name: "Production",
          last_seen_at: "2026-07-02T10:00:00Z",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(() => useRuntimeEngines({ projectId: "project-a" }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/engines?");
    expect(result.current.data?.engines[0].status).toBe("online");
  });

  it("fetches runtime estate overview", async () => {
    mockFetchOnce({
      overview: {
        project_id: "project-a",
        sources_total: 1,
        sources_failed: 0,
        sources_partial: 0,
        sources_demo: 1,
        engines_total: 2,
        engines_online: 1,
        engines_degraded: 1,
        engines_offline: 0,
        engines_unknown: 0,
        engines_unhealthy: 1,
        jobs_unhealthy: 1,
        plans_unhealthy: 1,
        sources_stale: 1,
        environments_total: 2,
        workspaces_total: 1,
        last_synced_at: "2026-07-02T10:00:00Z",
      },
    });

    const { result } = renderHook(() => useRuntimeOverview({ projectId: "project-a" }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.overview.engines_total).toBe(2);
    expect(result.current.data?.overview.engines_unhealthy).toBe(1);
  });

  it("fetches runtime engine health", async () => {
    const fetchSpy = mockFetchOnce({
      health: [
        {
          project_id: "project-a",
          source_key: "mock",
          engine_id: "engine-prod-02",
          engine_name: "remote-engine-prod-02",
          engine_status: "degraded",
          environment_name: "Production",
          workspace_name: "ESB Main",
          total_runs: 2,
          failed_runs: 1,
          failure_rate: 0.5,
          current_failure_streak: 1,
          latest_run_at: "2026-07-02T10:00:00Z",
          latest_state: "failed",
          p50_duration_ms: 32000,
          p95_duration_ms: 32000,
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeEngineHealth({ projectId: "project-a", status: "degraded" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/engine-health?");
    expect(url).toContain("status=degraded");
    expect(result.current.data?.health[0].current_failure_streak).toBe(1);
  });

  it("fetches runtime jobs", async () => {
    const fetchSpy = mockFetchOnce({
      jobs: [
        {
          job_id: "job-order-sync",
          source_key: "mock",
          name: "esb_order_sync_route",
          display_name: "Order Sync Route",
          job_type: "task",
          status: "active",
          environment_name: "Production",
          workspace_name: "ESB Main",
          engine_id: "engine-prod-02",
          engine_name: "remote-engine-prod-02",
          owner: "integration-ops",
          last_seen_at: "2026-07-02T10:00:00Z",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeJobs({ projectId: "project-a", engineId: "engine-prod-02" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/jobs?");
    expect(url).toContain("project_id=project-a");
    expect(url).toContain("engine_id=engine-prod-02");
    expect(result.current.data?.jobs[0].display_name).toBe("Order Sync Route");
  });

  it("fetches runtime artifacts", async () => {
    const fetchSpy = mockFetchOnce({
      artifacts: [
        {
          artifact_id: "artifact-1",
          source_key: "mock",
          name: "orders_route",
          artifact_type: "route",
          version: "1.2.3",
          workspace_name: "ESB Main",
          owner: "integration-ops",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeArtifacts({ projectId: "project-a", artifactType: "route" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/artifacts?");
    expect(url).toContain("artifact_type=route");
    expect(result.current.data?.artifacts[0].version).toBe("1.2.3");
  });

  it("fetches runtime connections", async () => {
    const fetchSpy = mockFetchOnce({
      connections: [
        {
          connection_id: "conn-1",
          source_key: "mock",
          name: "Oracle Orders",
          connection_type: "oracle",
          workspace_name: "ESB Main",
          owner: "dba",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeConnections({ projectId: "project-a", connectionType: "oracle" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/connections?");
    expect(url).toContain("connection_type=oracle");
    expect(result.current.data?.connections[0].name).toBe("Oracle Orders");
  });

  it("fetches runtime dependency usage", async () => {
    const fetchSpy = mockFetchOnce({
      dependencies: [
        {
          job_id: "job-1",
          job_name: "nightly-load",
          source_key: "mock",
          dependency_type: "connection",
          dependency_id: "conn-1",
          dependency_name: "Oracle Orders",
          dependency_kind: "oracle",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeDependencies({ dependencyType: "connection", dependencyId: "conn-1" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/dependencies?");
    expect(url).toContain("dependency_type=connection");
    expect(url).toContain("dependency_id=conn-1");
    expect(result.current.data?.dependencies[0].job_name).toBe("nightly-load");
  });

  it("fetches runtime dependency summary", async () => {
    const fetchSpy = mockFetchOnce({
      summary: {
        availability: {
          artifacts_supported: true,
          connections_supported: true,
          schedules_supported: false,
          promotions_supported: false,
          clusters_supported: false,
          unavailable: ["schedules", "promotions", "clusters"],
        },
        artifacts_total: 2,
        connections_total: 1,
        dependencies_total: 4,
        stale_artifacts: [],
        unowned_artifacts: [],
        unowned_connections: [],
      },
    });

    const { result } = renderHook(
      () => useRuntimeDependencySummary({ projectId: "project-a" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/dependency-summary?");
    expect(result.current.data?.summary.availability.unavailable).toContain("schedules");
  });

  it("fetches runtime job runs", async () => {
    const fetchSpy = mockFetchOnce({
      runs: [
        {
          run_id: "esbmain-route-7841",
          job_id: "job-order-sync",
          job_name: "esb_order_sync_route",
          source_key: "mock",
          execution_id: "esbmain-route-7841",
          state: "failed",
          engine_name: "remote-engine-prod-02",
          started_at: "2026-07-02T10:00:00Z",
          ended_at: "2026-07-02T10:00:32Z",
          duration_ms: 32000,
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeJobRuns({ projectId: "project-a", state: "failed" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/job-runs?");
    expect(url).toContain("state=failed");
    expect(result.current.data?.runs[0].execution_id).toBe("esbmain-route-7841");
  });

  it("fetches runtime job health", async () => {
    mockFetchOnce({
      health: [
        {
          project_id: "project-a",
          source_key: "mock",
          job_id: "job-order-sync",
          job_name: "esb_order_sync_route",
          environment_name: "Production",
          workspace_name: "ESB Main",
          engine_name: "remote-engine-prod-02",
          total_runs: 2,
          failed_runs: 1,
          failure_rate: 0.5,
          current_failure_streak: 1,
          latest_run_at: "2026-07-02T10:00:00Z",
          latest_state: "failed",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(() => useRuntimeJobHealth({ projectId: "project-a" }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.health[0].current_failure_streak).toBe(1);
  });

  it("fetches runtime plans", async () => {
    const fetchSpy = mockFetchOnce({
      plans: [
        {
          plan_id: "plan-nightly-order-flow",
          source_key: "mock",
          name: "nightly_order_flow",
          display_name: "Nightly Order Flow",
          status: "active",
          environment_name: "Production",
          workspace_name: "ESB Main",
          owner: "integration-ops",
          step_count: 2,
          steps: [
            { step_id: "step-order-sync", name: "Run order sync", job_id: "job-order-sync", job_name: "esb_order_sync_route", order: 1 },
          ],
          last_seen_at: "2026-07-02T10:00:00Z",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(() => useRuntimePlans({ projectId: "project-a" }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/plans?");
    expect(url).toContain("project_id=project-a");
    expect(result.current.data?.plans[0].steps[0].job_id).toBe("job-order-sync");
  });

  it("fetches runtime plan runs", async () => {
    const fetchSpy = mockFetchOnce({
      runs: [
        {
          run_id: "plan-nightly-order-flow-7841",
          plan_id: "plan-nightly-order-flow",
          plan_name: "nightly_order_flow",
          source_key: "mock",
          state: "failed",
          started_at: "2026-07-02T10:00:00Z",
          ended_at: "2026-07-02T10:00:32Z",
          duration_ms: 32000,
          step_results: [
            { step_id: "step-order-sync", job_id: "job-order-sync", job_name: "esb_order_sync_route", state: "failed", execution_id: "esbmain-route-7841" },
          ],
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimePlanRuns({ projectId: "project-a", state: "failed" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/plan-runs?");
    expect(url).toContain("state=failed");
    expect(result.current.data?.runs[0].step_results[0].execution_id).toBe("esbmain-route-7841");
  });

  it("fetches runtime plan health", async () => {
    mockFetchOnce({
      health: [
        {
          project_id: "project-a",
          source_key: "mock",
          plan_id: "plan-nightly-order-flow",
          plan_name: "nightly_order_flow",
          environment_name: "Production",
          workspace_name: "ESB Main",
          total_runs: 2,
          failed_runs: 1,
          failure_rate: 0.5,
          current_failure_streak: 1,
          latest_run_at: "2026-07-02T10:00:00Z",
          latest_finish_at: "2026-07-02T10:00:32Z",
          latest_state: "failed",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(() => useRuntimePlanHealth({ projectId: "project-a" }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.health[0].failure_rate).toBe(0.5);
  });

  it("does not fetch logs when no execution is selected", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    renderHook(() => useRuntimeExecutionLogs(null, { minSeverity: "error" }), {
      wrapper: createWrapper(),
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("includes min_severity in the logs query when selected", async () => {
    const fetchSpy = mockFetchOnce({ execution_id: "e1", events: [], total: 0 });

    const { result } = renderHook(
      () => useRuntimeExecutionLogs("e1", { minSeverity: "error", limit: 1000 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/executions/e1/logs?");
    expect(url).toContain("min_severity=error");
  });
});

describe("useRuntimeLogs — insight hooks", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(() => vi.restoreAllMocks());

  const ESTATE_CONTEXT = {
    source_key: "mock",
    source_label: "Demo runtime source",
    source_type: "mock",
    source_status: "success",
    source_is_demo: true,
    source_last_synced_at: "2026-06-17T10:00:00Z",
    source_last_seen_at: "2026-06-17T10:00:00Z",
    environment_id: "env-prod",
    environment_name: "Production",
    workspace_id: "ws-esb-main",
    workspace_name: "ESB Main",
    engine_id: "engine-prod-02",
    engine_name: "remote-engine-prod-02",
    engine_status: "online",
    engine_version: "2.13.8",
    engine_last_seen_at: "2026-06-17T10:00:00Z",
    job_id: "job-order-sync",
    job_name: "esb_order_sync_route",
    job_display_name: "Order Sync Route",
    job_run_id: "esbmain-route-7841",
    plan_id: "plan-nightly-order-flow",
    plan_name: "nightly_order_flow",
    plan_display_name: "Nightly Order Flow",
    plan_run_id: "plan-nightly-order-flow-7841",
  };

  const SUMMARY_PAYLOAD = {
    summary: {
      project_id: "esbmain",
      execution_id: "esbmain-route-7841",
      job_name: "esb_order_sync_route",
      warning_count: 3,
      error_count: 2,
      first_warning_at: "2026-06-17T10:00:10Z",
      first_error_at: "2026-06-17T10:00:30Z",
      final_error_at: "2026-06-17T10:00:45Z",
      failed_component: "tOracleConnection_1",
      primary_error_code: "ORA-12170",
      primary_exception_class: "SQLRecoverableException",
      summary_text: "Oracle connection timed out on source DB",
      evidence_refs: [1, 2],
      state: "failed",
      started_at: "2026-06-17T10:00:00Z",
      ended_at: "2026-06-17T10:00:45Z",
      duration_ms: 45000,
      project_name: "esbmain",
      incident_id: "abc123def456abc123def456abc12300",
      has_incident: true,
      estate_context: ESTATE_CONTEXT,
    },
  };

  const INCIDENT_PAYLOAD = {
    incident: {
      project_id: "esbmain",
      incident_id: "abc123def456abc123def456abc12300",
      execution_id: "esbmain-route-7841",
      signature_hash: null,
      symptom_summary: "Oracle source database connection timed out",
      root_cause_candidate: "Source database TCP connection timeout",
      confidence: "high",
      first_bad_at: "2026-06-17T10:00:30Z",
      last_healthy_at: null,
      next_checks: ["Check Oracle listener", "Verify network path"],
      job_name: "esb_order_sync_route",
      state: "failed",
      estate_context: ESTATE_CONTEXT,
    },
  };

  const EVIDENCE_PAYLOAD = {
    incident_id: "abc123def456abc123def456abc12300",
    evidence: [
      {
        log_event_id: 1,
        evidence_role: "trigger",
        excerpt: "ORA-12170 timeout",
        timestamp: "2026-06-17T10:00:30Z",
        severity: "error",
      },
    ],
    total: 1,
  };

  const TIMELINE_PAYLOAD = {
    execution_id: "esbmain-route-7841",
    events: [
      {
        log_event_id: 1,
        timestamp: "2026-06-17T10:00:30Z",
        severity: "error",
        signal: "component=tOracleConnection_1; error_code=ORA-12170",
        component_name: "tOracleConnection_1",
        error_code: "ORA-12170",
        exception_class: null,
        retry_attempt: null,
        retry_max: null,
        confidence: "high",
        message_family: "oracle_error",
      },
    ],
    total: 1,
  };

  it("useRuntimeExecutionSummary builds correct URL", async () => {
    const fetchSpy = mockFetchOnce(SUMMARY_PAYLOAD);

    const { result } = renderHook(
      () => useRuntimeExecutionSummary("esbmain-route-7841"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toBe("/api/runtime/executions/esbmain-route-7841/summary");
  });

  it("useRuntimeExecutionSummary does not fetch when executionId is null", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    renderHook(() => useRuntimeExecutionSummary(null), { wrapper: createWrapper() });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("useRuntimeExecutionSummary parses a valid summary payload", async () => {
    mockFetchOnce(SUMMARY_PAYLOAD);

    const { result } = renderHook(
      () => useRuntimeExecutionSummary("esbmain-route-7841"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.summary.primary_error_code).toBe("ORA-12170");
    expect(result.current.data?.summary.has_incident).toBe(true);
    expect(result.current.data?.summary.incident_id).toBe(
      "abc123def456abc123def456abc12300",
    );
    expect(result.current.data?.summary.estate_context.engine_name).toBe(
      "remote-engine-prod-02",
    );
  });

  it("useRuntimeExecutionTimeline builds query string with min_severity", async () => {
    const fetchSpy = mockFetchOnce(TIMELINE_PAYLOAD);

    const { result } = renderHook(
      () =>
        useRuntimeExecutionTimeline("esbmain-route-7841", {
          minSeverity: "warning",
          limit: 100,
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/executions/esbmain-route-7841/timeline");
    expect(url).toContain("min_severity=warning");
    expect(url).toContain("limit=100");
  });

  it("useRuntimeExecutionTimeline parses a valid timeline payload", async () => {
    mockFetchOnce(TIMELINE_PAYLOAD);

    const { result } = renderHook(
      () => useRuntimeExecutionTimeline("esbmain-route-7841"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.events[0].error_code).toBe("ORA-12170");
    expect(result.current.data?.events[0].component_name).toBe("tOracleConnection_1");
  });

  it("useRuntimeIncident does not fetch when incidentId is null", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    renderHook(() => useRuntimeIncident(null), { wrapper: createWrapper() });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("useRuntimeIncident parses a valid incident payload", async () => {
    mockFetchOnce(INCIDENT_PAYLOAD);

    const { result } = renderHook(
      () => useRuntimeIncident("abc123def456abc123def456abc12300"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.incident.root_cause_candidate).toBe(
      "Source database TCP connection timeout",
    );
    expect(result.current.data?.incident.next_checks).toHaveLength(2);
    expect(result.current.data?.incident.estate_context.plan_display_name).toBe(
      "Nightly Order Flow",
    );
  });

  it("useRuntimeIncidentEvidence does not fetch when incidentId is null", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    renderHook(() => useRuntimeIncidentEvidence(null), { wrapper: createWrapper() });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("useRuntimeIncidentEvidence parses a valid evidence payload", async () => {
    mockFetchOnce(EVIDENCE_PAYLOAD);

    const { result } = renderHook(
      () => useRuntimeIncidentEvidence("abc123def456abc123def456abc12300", { limit: 5 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.evidence[0].evidence_role).toBe("trigger");
    expect(result.current.data?.evidence[0].severity).toBe("error");
  });

  it("insight hook surfaces an error on non-ok response", async () => {
    mockFetchOnce({}, false, 500);

    const { result } = renderHook(
      () => useRuntimeExecutionSummary("esbmain-route-7841"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  const SIMILAR_PAYLOAD = {
    incident_id: "abc123def456abc123def456abc12300",
    execution_id: "esbmain-route-7841",
    recurrence: {
      signature_hash: "aabbcc",
      signature_summary: "ORA-12170 / tOracleConnection_1",
      occurrence_count: 3,
      similar_count: 2,
      first_seen_at: "2026-06-01T00:00:00Z",
      last_seen_at: "2026-06-17T00:00:00Z",
      is_recurring: true,
      alert_fingerprint: "fp123fp123fp123fp123fp123fp12300",
      dedupe_key: "esbmain:aabbcc123456",
      alert_title: "ORA-12170 / tOracleConnection_1",
      alert_reason: "Seen 3 time(s) in this project.",
    },
    similar_incidents: [],
  };

  it("useRuntimeSimilarFailures builds correct URL with limit", async () => {
    const fetchSpy = mockFetchOnce(SIMILAR_PAYLOAD);

    const { result } = renderHook(
      () => useRuntimeSimilarFailures("abc123def456abc123def456abc12300", { limit: 5 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/incidents/abc123def456abc123def456abc12300/similar");
    expect(url).toContain("limit=5");
  });

  it("useRuntimeSimilarFailures parses recurrence fields", async () => {
    mockFetchOnce(SIMILAR_PAYLOAD);

    const { result } = renderHook(
      () => useRuntimeSimilarFailures("abc123def456abc123def456abc12300"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.recurrence.is_recurring).toBe(true);
    expect(result.current.data?.recurrence.occurrence_count).toBe(3);
    expect(result.current.data?.recurrence.alert_fingerprint).toBe("fp123fp123fp123fp123fp123fp12300");
  });

  it("useRuntimeSimilarFailures does not fetch when incidentId is null", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    renderHook(() => useRuntimeSimilarFailures(null), { wrapper: createWrapper() });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

// --- Aggregate rollups ---

describe("useRuntimeLogs aggregate rollups", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(() => vi.restoreAllMocks());

  const TREND_PAYLOAD = {
    bucket: "day",
    points: [
      {
        bucket_start: "2026-08-16T00:00:00Z",
        total_runs: 67,
        success_runs: 67,
        failed_runs: 0,
        running_runs: 0,
        unknown_runs: 0,
        failure_rate: 0,
        p50_duration_ms: 2700,
        p95_duration_ms: 8100,
        max_duration_ms: 12000,
      },
    ],
    total: 1,
  };

  it("useRuntimeTrend passes bucket and window through", async () => {
    const fetchSpy = mockFetchOnce(TREND_PAYLOAD);

    const { result } = renderHook(
      () =>
        useRuntimeTrend({
          projectId: "werkorder",
          bucket: "day",
          since: "2026-08-01T00:00:00Z",
          limit: 30,
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/trend?");
    expect(url).toContain("project_id=werkorder");
    expect(url).toContain("bucket=day");
    expect(url).toContain("limit=30");
    expect(result.current.data?.points[0].p95_duration_ms).toBe(8100);
  });

  it("useRuntimeTrend accepts null duration percentiles", async () => {
    mockFetchOnce({
      bucket: "week",
      points: [
        {
          bucket_start: "2026-08-10T00:00:00Z",
          total_runs: 3,
          success_runs: 0,
          failed_runs: 0,
          running_runs: 3,
          unknown_runs: 0,
          failure_rate: 0,
          p50_duration_ms: null,
          p95_duration_ms: null,
          max_duration_ms: null,
        },
      ],
      total: 1,
    });

    const { result } = renderHook(() => useRuntimeTrend({ bucket: "week" }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.bucket).toBe("week");
    expect(result.current.data?.points[0].p50_duration_ms).toBeNull();
  });

  it("useRuntimeTrend does not fetch when disabled", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    renderHook(() => useRuntimeTrend({}, false), { wrapper: createWrapper() });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("useRuntimeJobReliability parses streaks and unfinished counts", async () => {
    const fetchSpy = mockFetchOnce({
      jobs: [
        {
          project_id: "werkorder",
          source_key: "talend_tmc",
          job_name: "RHM2Smartflow_Workorders",
          total_runs: 924,
          success_runs: 378,
          failed_runs: 546,
          unfinished_runs: 0,
          failure_rate: 0.591,
          current_failure_streak: 4,
          first_run_at: "2026-05-26T00:00:00Z",
          last_run_at: "2026-07-01T00:00:00Z",
          latest_state: "failed",
          p50_duration_ms: 23700,
          p95_duration_ms: 33700,
          max_duration_ms: 41000,
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeJobReliability({ projectId: "werkorder", limit: 100, offset: 0 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchSpy.mock.calls[0][0] as string).toContain("/api/runtime/job-reliability?");
    expect(result.current.data?.jobs[0].current_failure_streak).toBe(4);
    expect(result.current.data?.jobs[0].latest_state).toBe("failed");
    expect(result.current.data?.total).toBe(1);
  });

  it("useRuntimeComponentHotspots sends component filters", async () => {
    const fetchSpy = mockFetchOnce({
      components: [
        {
          project_id: "werkorder",
          source_key: "talend_tmc",
          component_key: "tHashOutput_1",
          component_type: "tHashOutput",
          source_connector_type: "tHashOutput",
          target_connector_type: "tJavaFlex",
          executions: 348,
          rows_total: 1128895,
          rows_rejected: null,
          reject_ratio: null,
          avg_duration_ms: 17797,
          p95_duration_ms: 24000,
          max_duration_ms: 31000,
          last_measured_at: "2026-08-16T22:00:00Z",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeComponentHotspots({ componentType: "tHashOutput", limit: 20 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/component-hotspots?");
    expect(url).toContain("component_type=tHashOutput");
    expect(result.current.data?.components[0].rows_total).toBe(1128895);
    expect(result.current.data?.components[0].rows_rejected).toBeNull();
  });

  it("useRuntimeJobThroughput parses rows moved per job", async () => {
    const fetchSpy = mockFetchOnce({
      jobs: [
        {
          project_id: "werkorder",
          source_key: "talend_tmc",
          job_name: "Maximo2RHM_Workorders",
          executions_with_metrics: 349,
          component_count: 13,
          rows_total: 7066404,
          rows_rejected: null,
          reject_ratio: null,
          last_measured_at: "2026-08-16T22:00:00Z",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeJobThroughput({ projectId: "werkorder" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchSpy.mock.calls[0][0] as string).toContain("/api/runtime/job-throughput?");
    expect(result.current.data?.jobs[0].executions_with_metrics).toBe(349);
    expect(result.current.data?.jobs[0].component_count).toBe(13);
  });

  it("useRuntimeErrorSignatures parses recurrence windows and omits absent optionals", async () => {
    const fetchSpy = mockFetchOnce({
      signatures: [
        {
          project_id: "werkorder",
          signature_hash: "a".repeat(64),
          exception_class: "BatchUpdateException",
          summary_text: "BatchUpdateException",
          occurrence_count: 16,
          first_seen_at: "2026-07-27T00:00:00Z",
          last_seen_at: "2026-07-27T23:00:00Z",
        },
      ],
      total: 1,
    });

    const { result } = renderHook(
      () => useRuntimeErrorSignatures({ exceptionClass: "BatchUpdateException" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/api/runtime/error-signatures?");
    expect(url).toContain("exception_class=BatchUpdateException");
    const signature = result.current.data?.signatures[0];
    expect(signature?.occurrence_count).toBe(16);
    // error_code, component_name and causal_message are absent, not null.
    expect(signature?.error_code).toBeUndefined();
    expect(signature?.causal_message).toBeUndefined();
  });

  it("useRuntimeErrorSignatures surfaces causal_message once the backend sends it", async () => {
    mockFetchOnce({
      signatures: [
        {
          project_id: "cdc",
          signature_hash: "b".repeat(64),
          exception_class: "BigQueryException",
          summary_text: "BigQueryException",
          causal_message:
            'tBigQueryOutput_2_tBQBE Invalid dataset ID "test_ zaf_del_clean".',
          occurrence_count: 5,
          first_seen_at: null,
          last_seen_at: null,
        },
      ],
      total: 1,
    });

    const { result } = renderHook(() => useRuntimeErrorSignatures({ projectId: "cdc" }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.signatures[0].causal_message).toContain("Invalid dataset ID");
  });

  it("aggregate hooks do not fetch when disabled", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    const wrapper = createWrapper();
    renderHook(() => useRuntimeJobReliability({}, false), { wrapper });
    renderHook(() => useRuntimeComponentHotspots({}, false), { wrapper });
    renderHook(() => useRuntimeJobThroughput({}, false), { wrapper });
    renderHook(() => useRuntimeErrorSignatures({}, false), { wrapper });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
