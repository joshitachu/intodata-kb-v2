import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createFleetClient,
  deleteFleetClient,
  fetchFleetClients,
  rotateTelemetryKey,
  updateFleetClient,
  type ClientEnv,
  type CreateClientRequest,
  type FleetClient,
  type UpdateClientRequest,
} from "../api/fleet";
import { FLEET_SNAPSHOTS_KEY } from "./useFleet";

export const FLEET_CLIENTS_KEY = ["fleet-clients"] as const;

/**
 * A heartbeat key as returned once by Mission Control. It is handed to the
 * caller's callback and never returned from a mutation, so it cannot land in
 * the React Query cache; the caller keeps it in component state only.
 */
export interface OneTimeClientKey {
  slug: string;
  telemetryKey: string;
  clientEnv: ClientEnv;
}

export function useFleetClients() {
  return useQuery({
    queryKey: FLEET_CLIENTS_KEY,
    queryFn: fetchFleetClients,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}

function useInvalidateFleet() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: FLEET_CLIENTS_KEY });
    void qc.invalidateQueries({ queryKey: FLEET_SNAPSHOTS_KEY });
  };
}

/**
 * Register a client. The mutation result is the client record only; the key
 * and env block go to `onKey`. The request may carry a control key, so callers
 * should `reset()` after settling to drop the variables from the cache too.
 */
export function useAddFleetClient(onKey: (key: OneTimeClientKey) => void) {
  const invalidate = useInvalidateFleet();
  return useMutation({
    mutationFn: async (body: CreateClientRequest): Promise<FleetClient> => {
      const { client, telemetry_key, client_env } = await createFleetClient(body);
      onKey({ slug: client.slug, telemetryKey: telemetry_key, clientEnv: client_env });
      return client;
    },
    gcTime: 0,
    onSuccess: invalidate,
  });
}

/** Edit the instance URL and/or control key. Callers `reset()` after settling (control key in variables). */
export function useUpdateFleetClient(slug: string) {
  const invalidate = useInvalidateFleet();
  return useMutation({
    mutationFn: (body: UpdateClientRequest) => updateFleetClient(slug, body),
    gcTime: 0,
    onSuccess: invalidate,
  });
}

/** Rotate the heartbeat key. The mutation resolves to nothing; the new key goes to `onKey`. */
export function useRotateTelemetryKey(slug: string, onKey: (key: OneTimeClientKey) => void) {
  const invalidate = useInvalidateFleet();
  return useMutation({
    mutationFn: async (): Promise<void> => {
      const { telemetry_key, client_env } = await rotateTelemetryKey(slug);
      onKey({ slug, telemetryKey: telemetry_key, clientEnv: client_env });
    },
    gcTime: 0,
    onSuccess: invalidate,
  });
}

export function useDeleteFleetClient(slug: string) {
  const invalidate = useInvalidateFleet();
  return useMutation({
    mutationFn: () => deleteFleetClient(slug),
    onSuccess: invalidate,
  });
}
