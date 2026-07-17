import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useJobGraph } from "@/hooks/useJobGraph";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockJobGraph(overrides: Record<string, unknown> = {}) {
  return {
    job_name: "jobA",
    group: "Item",
    nodes: [
      {
        unique_name: "tMap_1",
        component_type: "tMap",
        role: "transform",
        has_subgraph: true,
        subjob_target: null,
        connection_ref: "",
        param_summary: {},
        has_code: false,
      },
    ],
    edges: [
      {
        name: "row1",
        source: "tMap_1",
        target: "tMap_1",
        connector_name: "FLOW",
        line_style: 0,
      },
    ],
    tmap_tables: {},
    ...overrides,
  };
}

describe("useJobGraph", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches and Zod-parses the job graph, keyed by project and job", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockJobGraph()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useJobGraph("proj 1", "jobA"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.job_name).toBe("jobA");
    expect(result.current.data?.nodes).toHaveLength(1);
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/projects/proj%201/jobs/jobA/graph",
    );
  });

  it("does not fetch while jobName is null", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    const { result } = renderHook(() => useJobGraph("proj1", null), {
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

    const { result } = renderHook(() => useJobGraph("proj1", "missing"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toBe("Job not found");
  });

  it("rejects a response that violates the documented contract", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify(mockJobGraph({ nodes: [{ unique_name: "x" }] })),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const { result } = renderHook(() => useJobGraph("proj1", "jobA"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
