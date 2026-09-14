"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
import { RuntimeStatusResponse, RuntimeStatusResponseSchema } from "@/types";

async function fetchRuntimeStatus(): Promise<RuntimeStatusResponse> {
  return RuntimeStatusResponseSchema.parse(
    await fetchJson<unknown>("/api/runtime/status", {
      cache: "no-store",
      fallbackError: "Failed to fetch runtime status",
    }),
  );
}

export function useRuntimeStatus() {
  return useQuery({
    queryKey: ["runtime", "status"],
    queryFn: fetchRuntimeStatus,
    staleTime: 15 * 1000,
    refetchInterval: 30 * 1000,
  });
}
