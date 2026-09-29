import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchClientLicences,
  fetchLicencePlans,
  issueClientLicence,
  type IssueLicenceRequest,
  type IssuedLicenceResponse,
} from "../api/fleet";
import { FLEET_SNAPSHOTS_KEY } from "./useFleet";

export const LICENCE_PLANS_KEY = ["fleet-licence-plans"] as const;

export function clientLicencesKey(clientSlug: string) {
  return ["fleet-licences", clientSlug] as const;
}

export function useLicencePlans() {
  return useQuery({
    queryKey: LICENCE_PLANS_KEY,
    queryFn: fetchLicencePlans,
    staleTime: 5 * 60_000,
  });
}

export function useClientLicences(clientSlug: string) {
  return useQuery({
    queryKey: clientLicencesKey(clientSlug),
    queryFn: () => fetchClientLicences(clientSlug),
    staleTime: 15_000,
  });
}

/** What the mutation keeps: the issuance result WITHOUT the signed token. */
export type IssuedLicenceSummary = Omit<IssuedLicenceResponse, "token">;

/**
 * Issue a licence for one client.
 *
 * The signed token (only present when `return_token` was requested) is handed
 * to `onToken` and stripped from the mutation result, so it never lands in
 * the React Query mutation cache. The caller holds it in component state only.
 */
export function useIssueLicence(clientSlug: string, onToken: (token: string | null) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: IssueLicenceRequest): Promise<IssuedLicenceSummary> => {
      const { token, ...summary } = await issueClientLicence(clientSlug, body);
      onToken(token ?? null);
      return summary;
    },
    gcTime: 0,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: FLEET_SNAPSHOTS_KEY });
      void qc.invalidateQueries({ queryKey: clientLicencesKey(clientSlug) });
    },
  });
}
