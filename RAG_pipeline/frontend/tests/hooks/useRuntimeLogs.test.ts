import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useRuntimeExecutions,
  useRuntimeExecutionLogs,
  useRuntimeFailures,
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
