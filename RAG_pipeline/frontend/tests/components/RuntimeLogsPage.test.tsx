import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import RuntimeLogsPage from "@/app/admin/logs/page";
import RuntimeExecutionsPage from "@/app/admin/logs/executions/page";

const mocks = vi.hoisted(() => ({
  useRuntimeExecution: vi.fn(), useRuntimeExecutions: vi.fn(), useRuntimeHealth: vi.fn(),
  useRuntimeIncidents: vi.fn(), useRuntimeIncident: vi.fn(), useRuntimeJobs: vi.fn(),
  useRuntimeOverview: vi.fn(), useRuntimePlans: vi.fn(), useRuntimeSources: vi.fn(),
  useRuntimeEngines: vi.fn(), useUserProjects: vi.fn(), handleSendMessage: vi.fn(), push: vi.fn(),
}));

vi.mock("@/hooks/useRuntimeLogs", () => mocks);
vi.mock("@/hooks/useUserProjects", () => ({ useUserProjects: mocks.useUserProjects }));
vi.mock("@/contexts/ChatContext", () => ({ useChatContext: () => ({ handleSendMessage: mocks.handleSendMessage }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));

function queryState(data: unknown) {
  return { data, isLoading: false, isError: false, refetch: vi.fn() };
}

function setDefaults() {
  mocks.useUserProjects.mockReturnValue(queryState({ projects: [] }));
  mocks.useRuntimeOverview.mockReturnValue(queryState({ overview: { engines_online: 2, engines_total: 3, engines_unhealthy: 0, jobs_unhealthy: 1, plans_unhealthy: 0 } }));
  mocks.useRuntimeExecutions.mockReturnValue(queryState({ executions: [{ execution_id: "exec-1", job_name: "orders_sync", state: "failed", started_at: "2026-07-15T10:00:00Z", duration_ms: 1000 }], total: 1 }));
  mocks.useRuntimeHealth.mockReturnValue(queryState({ health: [{ job_name: "orders_sync", failure_rate: 1, current_failure_streak: 1 }], total: 1 }));
  mocks.useRuntimeIncidents.mockReturnValue(queryState({ incidents: [], total: 0 }));
  mocks.useRuntimeExecution.mockReturnValue(queryState({ execution: { execution_id: "exec-1", job_name: "orders_sync", state: "failed", started_at: "2026-07-15T10:00:00Z" } }));
  mocks.useRuntimeIncident.mockReturnValue(queryState({ incident: null }));
  mocks.useRuntimeJobs.mockReturnValue(queryState({ jobs: [], total: 0 }));
  mocks.useRuntimePlans.mockReturnValue(queryState({ plans: [], total: 0 }));
  mocks.useRuntimeSources.mockReturnValue(queryState({ sources: [], total: 0 }));
  mocks.useRuntimeEngines.mockReturnValue(queryState({ engines: [], total: 0 }));
}

describe("RuntimeLogsPage", () => {
  beforeEach(() => { vi.clearAllMocks(); setDefaults(); });

  it("renders a summary-first hub and does not invoke entity inventory queries", () => {
    render(React.createElement(RuntimeLogsPage));
    expect(screen.getByText("Operational console")).toBeTruthy();
    expect(screen.getByText("Outcome trend")).toBeTruthy();
    expect(screen.getByText("Needs attention")).toBeTruthy();
    expect(mocks.useRuntimeJobs).not.toHaveBeenCalled();
    expect(mocks.useRuntimePlans).not.toHaveBeenCalled();
  });

  it("opens an accessible execution drawer from a run row", () => {
    render(React.createElement(RuntimeLogsPage));
    fireEvent.click(screen.getAllByText("orders_sync")[0]);
    expect(screen.getByRole("dialog", { name: "Runtime details" })).toBeTruthy();
    expect(screen.getAllByText("exec-1").length).toBeGreaterThan(0);
  });

  it("sends the scoped question through the shared chat context", async () => {
    mocks.handleSendMessage.mockResolvedValue(undefined);
    render(React.createElement(RuntimeLogsPage));
    fireEvent.change(screen.getByPlaceholderText("What changed in the failed runs?"), { target: { value: "Why did orders fail?" } });
    fireEvent.click(screen.getByText("Ask Nexus"));
    await vi.waitFor(() => expect(mocks.handleSendMessage).toHaveBeenCalledWith({ query: "Why did orders fail?", project_id: undefined }));
    expect(mocks.push).toHaveBeenCalledWith("/");
  });

  it("uses execution-only queries on the runs route and exposes the honest server total", () => {
    render(React.createElement(RuntimeExecutionsPage));
    expect(screen.getByText("1 records")).toBeTruthy();
    expect(mocks.useRuntimeOverview).not.toHaveBeenCalled();
    expect(mocks.useRuntimeExecutions).toHaveBeenCalled();
  });
});
