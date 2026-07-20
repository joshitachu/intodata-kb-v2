import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useComponentGraph,
  ComponentGraphFallbackError,
} from "@/hooks/useComponentGraph";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockComponentGraph(overrides: Record<string, unknown> = {}) {
  return {
    project_id: "proj1",
    job_name: "jobA",
    component_unique_name: "tMap_1",
    input_tables: [],
    var_table: { table_name: "Var", columns: [], source_component: null },
    output_tables: [],
    edges: [],
    ...overrides,
  };
}

describe("useComponentGraph", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches and Zod-parses the component graph, keyed by project/job/component", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockComponentGraph()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useComponentGraph("proj 1", "jobA", "tMap_1"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.component_unique_name).toBe("tMap_1");
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/projects/proj%201/jobs/jobA/components/tMap_1/graph",
    );
  });

  it("does not fetch while componentName is null", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    const { result } = renderHook(() => useComponentGraph("proj1", "jobA", null), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("throws ComponentGraphFallbackError on the documented 404 fallback contract", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ fallback: "inspector" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(
      () => useComponentGraph("proj1", "jobA", "tDBOutput_1"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ComponentGraphFallbackError);
  });

  it("surfaces a generic error message for a non-fallback 404", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "Job not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(
      () => useComponentGraph("proj1", "missing", "tMap_1"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).not.toBeInstanceOf(ComponentGraphFallbackError);
    expect((result.current.error as Error).message).toBe("Job not found");
  });

  it("rejects a response that violates the documented contract", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify(mockComponentGraph({ component_unique_name: undefined })),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const { result } = renderHook(
      () => useComponentGraph("proj1", "jobA", "tMap_1"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
