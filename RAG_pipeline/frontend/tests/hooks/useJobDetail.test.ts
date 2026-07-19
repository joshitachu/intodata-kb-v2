import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useJobDetail } from "@/hooks/useJobDetail";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockJobDetail(overrides: Record<string, unknown> = {}) {
  return {
    project_id: "proj1",
    job_name: "jobA",
    group: "Item",
    scan_data: {
      components: [
        {
          unique_name: "tJava_1",
          component_type: "tJava",
          params: [{ name: "CODE", value: "System.out.println(1);" }],
          code: "System.out.println(1);",
          incoming_connections: [],
          outgoing_connections: [],
          metadata_connection_ref: "",
        },
      ],
    },
    doc_sections: [],
    summary: "",
    direction: "",
    ...overrides,
  };
}

describe("useJobDetail", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches and Zod-parses the job detail, keyed by project and job", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockJobDetail()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useJobDetail("proj 1", "jobA"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.scan_data.components).toHaveLength(1);
    expect(result.current.data?.scan_data.components[0].code).toBe(
      "System.out.println(1);",
    );
    expect(fetchSpy).toHaveBeenCalledWith("/api/projects/proj%201/jobs/jobA/detail");
  });

  it("does not fetch while jobName is null", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    const { result } = renderHook(() => useJobDetail("proj1", null), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("surfaces the proxy error message on a non-ok response", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "Job not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useJobDetail("proj1", "missing"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toBe("Job not found");
  });

  it("defaults scan_data.components to an empty list when absent", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockJobDetail({ scan_data: {} })), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useJobDetail("proj1", "jobA"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.scan_data.components).toEqual([]);
  });
});
