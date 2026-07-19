import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useFieldLineage, LineageDataUnavailableError } from "@/hooks/useFieldLineage";
import type { FieldLineageQuery } from "@/types/lineage";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockLineageResponse(overrides: Record<string, unknown> = {}) {
  return {
    project_id: "proj1",
    job_name: "jobA",
    start: { job_name: "jobA", table: "row17", column: "message", component: "tMap_1" },
    direction: "both",
    max_depth: 10,
    nodes: [],
    edges: [],
    truncated: false,
    ...overrides,
  };
}

const QUERY: FieldLineageQuery = {
  jobName: "jobA",
  table: "row17",
  column: "message",
  component: "tMap_1",
  direction: "both",
  maxDepth: 10,
};

describe("useFieldLineage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches and Zod-parses the field lineage, building the query string from the given query", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockLineageResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useFieldLineage("proj 1", QUERY), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.job_name).toBe("jobA");

    const calledUrl = fetchSpy.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/api/projects/proj%201/lineage/field?");
    expect(calledUrl).toContain("job=jobA");
    expect(calledUrl).toContain("table=row17");
    expect(calledUrl).toContain("column=message");
    expect(calledUrl).toContain("component=tMap_1");
    expect(calledUrl).toContain("direction=both");
    expect(calledUrl).toContain("max_depth=10");
  });

  it("does not fetch while the query is null", () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    const { result } = renderHook(() => useFieldLineage("proj1", null), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("throws LineageDataUnavailableError on a 404 (no scan/lineage.json for the project)", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "No lineage data for project 'proj1'" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useFieldLineage("proj1", QUERY), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(LineageDataUnavailableError);
    expect((result.current.error as Error).message).toBe("No lineage data for project 'proj1'");
  });

  it("surfaces a generic error for a non-404 failure", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "Project not found" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useFieldLineage("proj1", QUERY), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).not.toBeInstanceOf(LineageDataUnavailableError);
    expect((result.current.error as Error).message).toBe("Project not found");
  });

  it("rejects a response that violates the documented contract", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockLineageResponse({ job_name: undefined })), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useFieldLineage("proj1", QUERY), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
