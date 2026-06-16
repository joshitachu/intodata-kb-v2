import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRuntimeStatus } from "@/hooks/useRuntimeStatus";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockStatus(overrides = {}) {
  return {
    status: "connected",
    provider_key: "talend_tmc",
    provider_label: "Talend TMC",
    message: "Talend TMC connected",
    checked_at: "2026-06-16T00:00:00Z",
    ...overrides,
  };
}

describe("useRuntimeStatus", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts in loading state", () => {
    vi.spyOn(global, "fetch").mockImplementationOnce(
      () => new Promise<Response>(() => {})
    );

    const { result } = renderHook(() => useRuntimeStatus(), { wrapper: createWrapper() });
    expect(result.current.isLoading).toBe(true);
  });

  it("returns parsed status on success", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockStatus()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const { result } = renderHook(() => useRuntimeStatus(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.data?.status).toBe("connected");
    expect(result.current.data?.message).toBe("Talend TMC connected");
  });

  it("sets error state on fetch failure", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(null, { status: 500 })
    );

    const { result } = renderHook(() => useRuntimeStatus(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.data).toBeUndefined();
  });

  it("sets error state on malformed response", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ unexpected: "shape" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const { result } = renderHook(() => useRuntimeStatus(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});
