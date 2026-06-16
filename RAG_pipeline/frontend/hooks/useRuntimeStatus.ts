"use client";

import { useQuery } from "@tanstack/react-query";
import { RuntimeStatusResponse, RuntimeStatusResponseSchema } from "@/types";

async function fetchRuntimeStatus(): Promise<RuntimeStatusResponse> {
  const res = await fetch("/api/runtime/status", { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime status");
  }
  return RuntimeStatusResponseSchema.parse(await res.json());
}

export function useRuntimeStatus() {
  return useQuery({
    queryKey: ["runtime", "status"],
    queryFn: fetchRuntimeStatus,
    staleTime: 15 * 1000,
    refetchInterval: 30 * 1000,
  });
}
