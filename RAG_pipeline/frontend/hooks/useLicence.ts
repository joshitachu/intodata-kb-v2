"use client";

import {
  createContext,
  createElement,
  useContext,
  useEffect,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
import {
  LicenceStatusSchema,
  onLicenceRefused,
  type LicenceStatus,
} from "@/lib/licence";

/**
 * The instance's licence status, shared by the banner and every gated control.
 *
 * Read infrequently: on mount, every 10 minutes, and whenever any request in
 * the tab is refused with a 402 (`notifyLicenceRefused`). A licence changes
 * when an operator drops a new file in or when the period ends, not per
 * request, so polling faster buys nothing.
 */
export const LICENCE_POLL_MS = 10 * 60 * 1000;

export const LICENCE_STATUS_KEY = ["licence", "status"] as const;
/** The MCP server's view, which is the one that carries generation usage. */
export const MCP_LICENCE_STATUS_KEY = ["licence", "mcp-status"] as const;

async function fetchLicenceStatus(): Promise<LicenceStatus> {
  return LicenceStatusSchema.parse(
    await fetchJson<unknown>("/api/licence/status", {
      cache: "no-store",
      fallbackError: "Failed to read the licence status",
    }),
  );
}

async function fetchMcpLicenceStatus(): Promise<LicenceStatus> {
  return LicenceStatusSchema.parse(
    await fetchJson<unknown>("/api/source/licence", {
      cache: "no-store",
      fallbackError: "Failed to read the generation quota",
    }),
  );
}

export interface LicenceContextValue {
  /** Null until loaded, and when the status cannot be read. Null never blocks. */
  status: LicenceStatus | null;
  refresh: () => void;
}

const NO_LICENCE_INFO: LicenceContextValue = { status: null, refresh: () => {} };

/**
 * Exported for tests and for hosts that already hold a status. Outside a
 * provider every consumer sees "unknown", which blocks nothing -- the backend
 * remains the enforcer.
 */
export const LicenceContext = createContext<LicenceContextValue>(NO_LICENCE_INFO);

/** Re-read `queryKey` whenever any request in the tab gets a 402. */
function useRefetchOnRefusal(queryKey: readonly unknown[], enabled: boolean): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!enabled) return undefined;
    return onLicenceRefused(() => {
      void queryClient.invalidateQueries({ queryKey });
    });
    // `queryKey` is a module constant at every call site, so this is stable.
  }, [enabled, queryClient, queryKey]);
}

interface LicenceProviderProps {
  /** Only an authenticated user may read the status; pass false before login. */
  enabled: boolean;
  children: ReactNode;
}

export function LicenceProvider({ enabled, children }: LicenceProviderProps) {
  const query = useQuery({
    queryKey: LICENCE_STATUS_KEY,
    queryFn: fetchLicenceStatus,
    enabled,
    staleTime: LICENCE_POLL_MS,
    refetchInterval: enabled ? LICENCE_POLL_MS : false,
  });
  useRefetchOnRefusal(LICENCE_STATUS_KEY, enabled);

  const value: LicenceContextValue = {
    status: query.data ?? null,
    refresh: () => {
      void query.refetch();
    },
  };
  return createElement(LicenceContext.Provider, { value }, children);
}

export function useLicence(): LicenceContextValue {
  return useContext(LicenceContext);
}

/**
 * The MCP server's licence status, for surfaces that start scans or
 * generations. It is the authority for those and the only one that knows how
 * many generations this period has used.
 */
export function useMcpLicenceStatus(enabled = true) {
  const query = useQuery({
    queryKey: MCP_LICENCE_STATUS_KEY,
    queryFn: fetchMcpLicenceStatus,
    enabled,
    staleTime: LICENCE_POLL_MS,
    refetchInterval: enabled ? LICENCE_POLL_MS : false,
  });
  useRefetchOnRefusal(MCP_LICENCE_STATUS_KEY, enabled);
  return query;
}
