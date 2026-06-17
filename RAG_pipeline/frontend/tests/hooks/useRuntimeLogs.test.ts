import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useRuntimeExecutions,
  useRuntimeExecutionLogs,
  useRuntimeExecutionSummary,
  useRuntimeExecutionTimeline,
  useRuntimeIncident,
  useRuntimeIncidentEvidence,
  useRuntimeFailures,
  useRuntimeSimilarFailures,
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
        message: "[tOracleConnection_1] ORA-12170 Connect timeout",
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
