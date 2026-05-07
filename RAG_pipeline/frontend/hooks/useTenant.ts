"use client";

import { useQuery } from "@tanstack/react-query";
import {
  FALLBACK_TENANT,
  TenantConfig,
  TenantConfigSchema,
} from "@/types/tenant";

async function fetchTenant(): Promise<TenantConfig> {
  const res = await fetch("/api/tenant/config", { cache: "no-store" });
  if (!res.ok) return FALLBACK_TENANT;
  try {
    return TenantConfigSchema.parse(await res.json());
  } catch {
    return FALLBACK_TENANT;
  }
}

export function useTenant() {
  return useQuery({
    queryKey: ["tenant", "config"],
    queryFn: fetchTenant,
    // Tenant config is per-deployment and set at boot — no reason to refetch.
    staleTime: Infinity,
    initialData: FALLBACK_TENANT,
  });
}
