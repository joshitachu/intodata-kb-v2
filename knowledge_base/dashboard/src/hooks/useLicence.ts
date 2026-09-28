import { createContext, createElement, useContext, useEffect, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { onLicenceRefused, type LicenceStatus } from "../lib/licence";

/**
 * The instance's licence status (MCP server view, which includes this period's
 * generation usage), shared by the banner and the scan/generate controls.
 *
 * Read on mount, every 10 minutes, and after any 402 in the app. A licence
 * changes when an operator installs a new one or a period ends, so a faster
 * poll would buy nothing -- see the polling rationale in `lib/queryClient.ts`.
 */
export const LICENCE_POLL_MS = 10 * 60 * 1000;
export const LICENCE_STATUS_KEY = ["licence-status"] as const;

export interface LicenceContextValue {
  /** Null until loaded and when unreadable. Null never blocks anything. */
  status: LicenceStatus | null;
}

/** Outside a provider every consumer sees "unknown", which blocks nothing. */
export const LicenceContext = createContext<LicenceContextValue>({ status: null });

export function LicenceProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: LICENCE_STATUS_KEY,
    queryFn: () => api.licenceStatus(),
    staleTime: LICENCE_POLL_MS,
    refetchInterval: LICENCE_POLL_MS,
  });

  useEffect(
    () =>
      onLicenceRefused(() => {
        void queryClient.invalidateQueries({ queryKey: LICENCE_STATUS_KEY });
      }),
    [queryClient],
  );

  return createElement(LicenceContext.Provider, { value: { status: query.data ?? null } }, children);
}

export function useLicence(): LicenceContextValue {
  return useContext(LicenceContext);
}
