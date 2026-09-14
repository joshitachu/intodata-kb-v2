"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
import { UserProjectsResponseSchema } from "@/types";

export function useUserProjects() {
  return useQuery({
    queryKey: ["user-projects"],
    queryFn: async () =>
      UserProjectsResponseSchema.parse(
        await fetchJson<unknown>("/api/projects", {
          fallbackError: "Failed to load projects",
        }),
      ),
  });
}
