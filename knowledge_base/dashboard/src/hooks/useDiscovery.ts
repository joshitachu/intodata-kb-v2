import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export function usePlatforms() {
  return useQuery({
    queryKey: ["platforms"],
    queryFn: api.listPlatforms,
    staleTime: 5 * 60_000,
  });
}

export function useModels() {
  return useQuery({
    queryKey: ["models"],
    queryFn: api.listModels,
    staleTime: 5 * 60_000,
  });
}
