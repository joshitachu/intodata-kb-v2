import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchAllSnapshots, triggerGenerate, triggerScan } from "../api/fleet";

export const FLEET_SNAPSHOTS_KEY = ["fleet-snapshots"] as const;

export function useFleet() {
  return useQuery({
    queryKey: FLEET_SNAPSHOTS_KEY,
    queryFn: fetchAllSnapshots,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}

export function useTriggerScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ clientSlug, projectId }: { clientSlug: string; projectId: string }) =>
      triggerScan(clientSlug, projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: FLEET_SNAPSHOTS_KEY }),
  });
}

export function useTriggerGenerate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ clientSlug, projectId }: { clientSlug: string; projectId: string }) =>
      triggerGenerate(clientSlug, projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: FLEET_SNAPSHOTS_KEY }),
  });
}
