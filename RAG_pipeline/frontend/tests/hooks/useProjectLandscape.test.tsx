import React from "react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LandscapeUnavailableError, useProjectLandscape } from "@/hooks/useProjectLandscape";

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const payload = { applications: [], systems: [], edges: [], channels: [], interfaces: [], diagnostics: [], unmapped: [], summary: {} };

describe("useProjectLandscape", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("validates the scanner response before making it available", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 })));
    const { result } = renderHook(() => useProjectLandscape("topology"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(payload);
  });

  it("exposes a missing landscape as the explicit rescan fallback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "missing" }), { status: 404 })));
    const { result } = renderHook(() => useProjectLandscape("legacy"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(LandscapeUnavailableError);
  });

  it("does not turn malformed landscape data into a legacy fallback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ applications: "not-an-array" }), { status: 200 })));
    const { result } = renderHook(() => useProjectLandscape("bad-scan"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).not.toBeInstanceOf(LandscapeUnavailableError);
  });
});
