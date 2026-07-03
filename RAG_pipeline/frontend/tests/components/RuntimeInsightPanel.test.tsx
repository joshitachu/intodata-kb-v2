import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RuntimeInsightPanel } from "@/components/runtime/RuntimeInsightPanel";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockFetchSequence(responses: Array<{ body: unknown; ok?: boolean; status?: number }>) {
  const spy = vi.spyOn(global, "fetch");
  for (const r of responses) {
    spy.mockResolvedValueOnce({
      ok: r.ok ?? true,
      status: r.status ?? 200,
      json: async () => r.body,
    } as Response);
  }
  return spy;
}

const SUMMARY_WITH_INCIDENT = {
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
    evidence_refs: [1],
    state: "failed",
    started_at: "2026-06-17T10:00:00Z",
    ended_at: "2026-06-17T10:00:45Z",
    duration_ms: 45000,
    project_name: "esbmain",
    incident_id: "abc123def456abc123def456abc12300",
    has_incident: true,
    estate_context: {
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
      engine_id: "remote-engine-prod-02",
      engine_name: "remote-engine-prod-02",
      engine_status: "online",
      engine_version: "2.13.8",
      engine_last_seen_at: "2026-06-17T10:00:00Z",
      job_id: "job-order-sync",
      job_name: "esb_order_sync_route",
      job_display_name: "Order Sync",
      job_run_id: "esbmain-route-7841",
      plan_id: "plan-nightly-order-flow",
      plan_name: "nightly_order_flow",
      plan_display_name: "Nightly Order Flow",
      plan_run_id: "plan-nightly-order-flow-7841",
    },
  },
};

const INCIDENT = {
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
    next_checks: ["Check Oracle listener", "Verify network path to DB"],
    job_name: "esb_order_sync_route",
    state: "failed",
    estate_context: SUMMARY_WITH_INCIDENT.summary.estate_context,
  },
};

const EVIDENCE = {
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

const TIMELINE = {
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

describe("RuntimeInsightPanel", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("renders failed component, ORA-12170, exception class, likely cause, evidence, next checks, and timeline", async () => {
    const SIMILAR_EMPTY = {
      incident_id: "abc123def456abc123def456abc12300",
      execution_id: "esbmain-route-7841",
      recurrence: {
        signature_hash: null, signature_summary: null,
        occurrence_count: 1, similar_count: 0,
        first_seen_at: null, last_seen_at: null,
        is_recurring: false,
        alert_fingerprint: "fp00000000000000000000000000000",
        dedupe_key: "esbmain:unknown",
        alert_title: "Unknown failure", alert_reason: "First occurrence in this project.",
      },
      similar_incidents: [],
    };
    // Order: summary, timeline, incident, evidence, similar
    mockFetchSequence([
      { body: SUMMARY_WITH_INCIDENT },
      { body: TIMELINE },
      { body: INCIDENT },
      { body: EVIDENCE },
      { body: SIMILAR_EMPTY },
    ]);

    render(
      React.createElement(RuntimeInsightPanel, { executionId: "esbmain-route-7841" }),
      { wrapper: createWrapper() },
    );

    // Wait until incident data (last sequential fetch) has rendered.
    await waitFor(() => {
      expect(screen.getByText("Source database TCP connection timeout")).toBeTruthy();
    });

    // Summary-derived facts (may also appear in timeline column so use getAllByText)
    expect(screen.getAllByText("tOracleConnection_1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("ORA-12170").length).toBeGreaterThan(0);
    expect(screen.getByText("SQLRecoverableException")).toBeTruthy();
    expect(screen.getByText("Runtime Context")).toBeTruthy();
    expect(screen.getByText("Demo runtime source (demo)")).toBeTruthy();
    expect(screen.getByText("Production")).toBeTruthy();
    expect(screen.getByText("ESB Main")).toBeTruthy();
    expect(screen.getByText("remote-engine-prod-02 (online, 2.13.8)")).toBeTruthy();
    expect(screen.getByText("Order Sync")).toBeTruthy();
    expect(screen.getByText("Nightly Order Flow")).toBeTruthy();

    // Incident-derived items
    expect(screen.getByText("Check Oracle listener")).toBeTruthy();
    expect(screen.getByText("Verify network path to DB")).toBeTruthy();

    // Evidence excerpt (loads after incident resolves)
    await waitFor(() => {
      expect(screen.getByText("ORA-12170 timeout")).toBeTruthy();
    });
  });

  it("shows no-summary message and does not crash when summary returns 404", async () => {
    mockFetchSequence([
      { body: { detail: "Execution not found" }, ok: false, status: 404 },
    ]);

    render(
      React.createElement(RuntimeInsightPanel, { executionId: "unknown-exec-id" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(
        screen.getByText("No smart summary available for this execution."),
      ).toBeTruthy();
    });
  });

  it("shows no-incident state when summary has has_incident=false", async () => {
    const summaryNoIncident = {
      summary: {
        ...SUMMARY_WITH_INCIDENT.summary,
        has_incident: false,
        incident_id: null,
        failed_component: null,
        primary_error_code: null,
      },
    };
    mockFetchSequence([
      { body: summaryNoIncident },
      { body: { execution_id: "esbmain-route-7840", events: [], total: 0 } },
    ]);

    render(
      React.createElement(RuntimeInsightPanel, { executionId: "esbmain-route-7840" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(screen.getByText("No incident was derived for this execution.")).toBeTruthy();
    });
  });

  it("renders explicit unknown/not reported estate values for partial context", async () => {
    const partialSummary = {
      summary: {
        ...SUMMARY_WITH_INCIDENT.summary,
        has_incident: false,
        incident_id: null,
        estate_context: {
          source_key: "mock",
          source_label: null,
          source_status: null,
          source_is_demo: false,
          source_last_synced_at: null,
          environment_name: null,
          workspace_name: null,
          engine_id: null,
          job_name: "esb_order_sync_route",
          plan_name: null,
        },
      },
    };
    mockFetchSequence([
      { body: partialSummary },
      { body: { execution_id: "esbmain-route-7841", events: [], total: 0 } },
    ]);

    render(
      React.createElement(RuntimeInsightPanel, { executionId: "esbmain-route-7841" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(screen.getByText("mock")).toBeTruthy();
    });
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not reported").length).toBeGreaterThan(0);
  });

  it("renders empty evidence and timeline states without throwing", async () => {
    const SIMILAR_EMPTY = {
      incident_id: "abc123def456abc123def456abc12300",
      execution_id: "esbmain-route-7841",
      recurrence: {
        signature_hash: null, signature_summary: null,
        occurrence_count: 1, similar_count: 0,
        first_seen_at: null, last_seen_at: null,
        is_recurring: false,
        alert_fingerprint: "fp00000000000000000000000000000",
        dedupe_key: "esbmain:unknown",
        alert_title: "Unknown failure", alert_reason: "First occurrence in this project.",
      },
      similar_incidents: [],
    };
    // Order: summary, timeline, incident, evidence, similar
    mockFetchSequence([
      { body: SUMMARY_WITH_INCIDENT },
      { body: { execution_id: "esbmain-route-7841", events: [], total: 0 } },
      { body: INCIDENT },
      {
        body: {
          incident_id: "abc123def456abc123def456abc12300",
          evidence: [],
          total: 0,
        },
      },
      { body: SIMILAR_EMPTY },
    ]);

    render(
      React.createElement(RuntimeInsightPanel, { executionId: "esbmain-route-7841" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(
        screen.getByText("No evidence excerpts were linked to this incident."),
      ).toBeTruthy();
    });
    expect(screen.getByText("No timeline events match the current filters.")).toBeTruthy();
  });

  it("renders recurring badge and prior incident when similar failures shows is_recurring=true", async () => {
    const SIMILAR_PAYLOAD = {
      incident_id: "abc123def456abc123def456abc12300",
      execution_id: "esbmain-route-7841",
      recurrence: {
        signature_hash: "aabb",
        signature_summary: "ORA-12170",
        occurrence_count: 3,
        similar_count: 2,
        first_seen_at: "2026-06-01T00:00:00Z",
        last_seen_at: "2026-06-17T00:00:00Z",
        is_recurring: true,
        alert_fingerprint: "fp123fp123fp123fp123fp123fp12300",
        dedupe_key: "esbmain:aabb123456",
        alert_title: "ORA-12170",
        alert_reason: "Seen 3 time(s) in this project.",
      },
      similar_incidents: [
        {
          incident_id: "prior-incident-id-00000000000000000",
          execution_id: "esbmain-route-7800",
          job_name: "esb_order_sync_route",
          state: "failed",
          first_bad_at: "2026-06-01T10:00:00Z",
          symptom_summary: "Oracle timeout",
          root_cause_candidate: "DB host unreachable",
          confidence: "medium",
        },
      ],
    };

    // Order: summary, timeline, incident, evidence, similar
    mockFetchSequence([
      { body: SUMMARY_WITH_INCIDENT },
      { body: TIMELINE },
      { body: INCIDENT },
      { body: EVIDENCE },
      { body: SIMILAR_PAYLOAD },
    ]);

    render(
      React.createElement(RuntimeInsightPanel, { executionId: "esbmain-route-7841" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(screen.getByText(/Recurring/)).toBeTruthy();
    });
    expect(screen.getByText(/3×/)).toBeTruthy();
    expect(screen.getByText("DB host unreachable")).toBeTruthy();
  });
});
