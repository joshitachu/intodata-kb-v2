"use client";

import {
  createContext,
  useContext,
  useEffect,
  type ReactNode,
} from "react";
import { useTenant } from "@/hooks/useTenant";
import { FALLBACK_TENANT, type TenantConfig } from "@/types/tenant";

interface TenantContextValue {
  tenant: TenantConfig;
  /** "production" hides the environment badge; everything else renders it. */
  isProduction: boolean;
}

const TenantContext = createContext<TenantContextValue>({
  tenant: FALLBACK_TENANT,
  isProduction: true,
});

export function TenantProvider({ children }: { children: ReactNode }) {
  const { data } = useTenant();
  const tenant = data ?? FALLBACK_TENANT;
  const isProduction = tenant.environment === "production";

  // Sync the browser <title> so the OS tab shows the tenant identity.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const suffix = isProduction
      ? ""
      : ` (${tenant.environment.toUpperCase()})`;
    const base = tenant.name || process.env.NEXT_PUBLIC_TENANT_NAME || "Nexus";
    document.title = `${base}${suffix}`;
  }, [tenant.name, tenant.environment, isProduction]);

  return (
    <TenantContext.Provider value={{ tenant, isProduction }}>
      {children}
    </TenantContext.Provider>
  );
}

export function useTenantContext() {
  return useContext(TenantContext);
}
