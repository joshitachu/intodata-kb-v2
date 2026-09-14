import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

// Both of these are static catalogues -- the platform strategies compiled into
// the server and the model registry it was deployed with. They change on
// deploy, never mid-session, and neither is polled. The 5-minute staleTime was
// already here; a global `refetchInterval` was overriding it and re-fetching
// both every 5 seconds regardless (measured: 339 + 336 calls in ~2h).
const DEPLOY_SCOPED_STALE_MS = 5 * 60_000;

export function usePlatforms() {
  return useQuery({
    queryKey: ["platforms"],
    queryFn: api.listPlatforms,
    staleTime: DEPLOY_SCOPED_STALE_MS,
  });
}

export function useModels() {
  return useQuery({
    queryKey: ["models"],
    queryFn: api.listModels,
    staleTime: DEPLOY_SCOPED_STALE_MS,
  });
}
