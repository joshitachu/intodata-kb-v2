import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import RuntimeLogsPage from "@/app/admin/logs/page";

const mocks = vi.hoisted(() => ({
  useRuntimeExecution: vi.fn(),
  useRuntimeExecutionLogs: vi.fn(),
  useRuntimeEngineHealth: vi.fn(),
  useRuntimeEngines: vi.fn(),
  useRuntimeExecutions: vi.fn(),
  useRuntimeFailures: vi.fn(),
  useRuntimeHealth: vi.fn(),
  useRuntimeJobHealth: vi.fn(),
  useRuntimeJobRuns: vi.fn(),
  useRuntimeJobs: vi.fn(),
  useRuntimeOverview: vi.fn(),
  useRuntimePlanHealth: vi.fn(),
  useRuntimePlanRuns: vi.fn(),
  useRuntimePlans: vi.fn(),
  useRuntimeSources: vi.fn(),
  useRuntimeArtifacts: vi.fn(),
  useRuntimeConnections: vi.fn(),
  useRuntimeDependencies: vi.fn(),
  useRuntimeDependencySummary: vi.fn(),
  useUserProjects: vi.fn(),
}));

vi.mock("@/hooks/useRuntimeLogs", () => mocks);

vi.mock("@/hooks/useUserProjects", () => ({
  useUserProjects: mocks.useUserProjects,
}));

vi.mock("@/components/runtime/RuntimeInsightPanel", () => ({
  RuntimeInsightPanel: ({ executionId }: { executionId: string }) =>
    React.createElement("div", null, `diagnosis for ${executionId}`),
}));

function queryState(data: unknown) {
  return {
    data,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  };
}

describe("RuntimeLogsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useUserProjects.mockReturnValue(queryState({ projects: [] }));
    // Estate inventory hooks — default to empty so the page renders in every view.
    mocks.useRuntimeArtifacts.mockReturnValue(queryState({ artifacts: [] }));
    mocks.useRuntimeConnections.mockReturnValue(queryState({ connections: [] }));
    mocks.useRuntimeDependencies.mockReturnValue(queryState({ dependencies: [] }));
    mocks.useRuntimeDependencySummary.mockReturnValue(queryState({ summary: null }));
    mocks.useRuntimeExecutions.mockReturnValue(queryState({ executions: [], total: 0 }));
    mocks.useRuntimeFailures.mockReturnValue(
      queryState({
        failures: [
          {
            execution_id: "failed-exec-1",
            job_name: "orders_sync",
            failed_at: "2026-07-02T10:00:00Z",
            error_summary: "Likely database timeout",
          },
        ],
        total: 1,
      }),
    );
    mocks.useRuntimeHealth.mockReturnValue(
      queryState({
        health: [
          {
            project_id: "project-a",
            source_key: "mock",
            job_name: "orders_sync",
            total_executions: 2,
            failed_executions: 1,
            failure_rate: 0.5,
            current_failure_streak: 1,
            last_failure_at: "2026-07-02T10:00:00Z",
          },
        ],
        total: 1,
      }),
    );
    mocks.useRuntimeSources.mockReturnValue(
      queryState({
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
      }),
    );
    mocks.useRuntimeEngines.mockReturnValue(
      queryState({
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
          {
            engine_id: "engine-prod-02",
            source_key: "mock",
            name: "remote-engine-prod-02",
            status: "degraded",
            version: "8.0.1",
            environment_name: "Production",
            last_seen_at: "2026-07-02T09:53:00Z",
          },
        ],
        total: 2,
      }),
    );
    mocks.useRuntimeOverview.mockReturnValue(
      queryState({
        overview: {
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
      }),
    );
    mocks.useRuntimeEngineHealth.mockReturnValue(
      queryState({
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
            p50_duration_ms: 30000,
            p95_duration_ms: 32000,
          },
        ],
        total: 1,
      }),
    );
    mocks.useRuntimeJobs.mockReturnValue(
      queryState({
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
            artifact_name: "esb-order-sync-1.8.4",
            last_seen_at: "2026-07-02T10:00:00Z",
          },
        ],
        total: 1,
      }),
    );
    mocks.useRuntimeJobRuns.mockReturnValue(
      queryState({
        runs: [
          {
            run_id: "failed-exec-1",
            job_id: "job-order-sync",
            job_name: "esb_order_sync_route",
            source_key: "mock",
            execution_id: "failed-exec-1",
            state: "failed",
            environment_name: "Production",
            workspace_name: "ESB Main",
            engine_id: "engine-prod-02",
            engine_name: "remote-engine-prod-02",
            started_at: "2026-07-02T09:59:30Z",
            ended_at: "2026-07-02T10:00:00Z",
            duration_ms: 30000,
          },
        ],
        total: 1,
      }),
    );
    mocks.useRuntimeJobHealth.mockReturnValue(
      queryState({
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
      }),
    );
    mocks.useRuntimePlans.mockReturnValue(
      queryState({
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
              {
                step_id: "step-order-sync",
                name: "Run order sync",
                job_id: "job-order-sync",
                job_name: "esb_order_sync_route",
                order: 1,
              },
              {
                step_id: "step-billing-export",
                name: "Run billing export",
                job_id: "job-billing-export",
                job_name: "esb_billing_export_route",
                order: 2,
              },
            ],
            last_seen_at: "2026-07-02T10:00:00Z",
          },
        ],
        total: 1,
      }),
    );
    mocks.useRuntimePlanRuns.mockReturnValue(
      queryState({
        runs: [
          {
            run_id: "plan-nightly-order-flow-7841",
            plan_id: "plan-nightly-order-flow",
            plan_name: "nightly_order_flow",
            source_key: "mock",
            state: "failed",
            started_at: "2026-07-02T09:59:30Z",
            ended_at: "2026-07-02T10:00:00Z",
            duration_ms: 30000,
            step_results: [
              {
                step_id: "step-order-sync",
                job_id: "job-order-sync",
                job_name: "esb_order_sync_route",
                state: "failed",
                execution_id: "failed-exec-1",
              },
            ],
          },
        ],
        total: 1,
      }),
    );
    mocks.useRuntimePlanHealth.mockReturnValue(
      queryState({
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
            latest_finish_at: "2026-07-02T10:00:00Z",
            latest_state: "failed",
          },
        ],
        total: 1,
      }),
    );
    mocks.useRuntimeExecution.mockReturnValue(
      queryState({
        execution: {
          execution_id: "failed-exec-1",
          job_name: "orders_sync",
          project_name: "project-a",
          state: "failed",
          started_at: "2026-07-02T09:59:30Z",
          ended_at: "2026-07-02T10:00:00Z",
          duration_ms: 30000,
        },
      }),
    );
    mocks.useRuntimeExecutionLogs.mockReturnValue(
      queryState({
        execution_id: "failed-exec-1",
        events: [
          {
            timestamp: "2026-07-02T10:00:00Z",
            severity: "error",
            message: "SECRET RAW LINE SHOULD NOT RENDER",
          },
        ],
        total: 1,
      }),
    );
  });

  it("defaults to jobs and renders runtime estate", () => {
    render(React.createElement(RuntimeLogsPage));

    expect(screen.getByText("Runtime Operations")).toBeTruthy();
    expect(screen.getAllByText("Runtime Sources").length).toBeGreaterThan(0);
    expect(screen.getByText("Runtime Engines")).toBeTruthy();
    expect(screen.getByText("Demo runtime source")).toBeTruthy();
    expect(screen.getByText("remote-engine-prod-01")).toBeTruthy();
    expect(screen.getAllByText("remote-engine-prod-02").length).toBeGreaterThan(0);
    expect(screen.getByText("Operational Health")).toBeTruthy();
    expect(screen.getByText("1 stale source")).toBeTruthy();
    expect(screen.getAllByText("Engines").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Jobs").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Plans").length).toBeGreaterThan(0);
    expect(screen.getByText("Unhealthy Components")).toBeTruthy();
    expect(screen.getAllByText("50%").length).toBeGreaterThan(0);
    expect(screen.getByText("Order Sync Route")).toBeTruthy();
    expect(screen.getAllByText("remote-engine-prod-02").length).toBeGreaterThan(0);
    expect(screen.getByText("integration-ops")).toBeTruthy();
  });

  it("renders plans and plan runs", () => {
    render(React.createElement(RuntimeLogsPage));

    fireEvent.click(screen.getByText("plans"));
    expect(screen.getByText("Nightly Order Flow")).toBeTruthy();
    expect(screen.getByText(/2 steps/)).toBeTruthy();
    expect(screen.getAllByText(/esb_order_sync_route/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByText("plan-runs"));
    expect(screen.getByText("plan-nightly-order-flow-7841")).toBeTruthy();
    expect(screen.getAllByText("nightly_order_flow").length).toBeGreaterThan(0);
    expect(screen.getByText(/esb_order_sync_route: failed/)).toBeTruthy();
  });

  it("opens diagnosis from a failed job run without raw log output", () => {
    render(React.createElement(RuntimeLogsPage));

    fireEvent.click(screen.getByText("runs"));
    expect(screen.getAllByText("esb_order_sync_route").length).toBeGreaterThan(0);
    expect(screen.getByText("execution failed-exec-1")).toBeTruthy();

    const runRow = screen.getByText("failed-exec-1").closest("tr");
    expect(runRow).not.toBeNull();
    fireEvent.click(runRow!);

    expect(screen.getByText("Failure Diagnosis")).toBeTruthy();
    expect(screen.getByText("diagnosis for failed-exec-1")).toBeTruthy();
    expect(screen.queryByText("Raw Logs")).toBeNull();
    expect(screen.queryByText("SECRET RAW LINE SHOULD NOT RENDER")).toBeNull();
    expect(mocks.useRuntimeExecutionLogs).not.toHaveBeenCalled();
  });

  it("keeps the failures view usable", () => {
    render(React.createElement(RuntimeLogsPage));

    fireEvent.click(screen.getByText("failures"));
    expect(screen.getByText("Likely database timeout")).toBeTruthy();

    const failureRow = screen.getByText("failed-exec-1").closest("tr");
    expect(failureRow).not.toBeNull();
    fireEvent.click(failureRow!);

    expect(screen.getByText("Failure Diagnosis")).toBeTruthy();
    expect(screen.getByText("diagnosis for failed-exec-1")).toBeTruthy();
    expect(screen.queryByText("Raw Logs")).toBeNull();
    expect(screen.queryByText("SECRET RAW LINE SHOULD NOT RENDER")).toBeNull();
    expect(mocks.useRuntimeExecutionLogs).not.toHaveBeenCalled();
  });
});
