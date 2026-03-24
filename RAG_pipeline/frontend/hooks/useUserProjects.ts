"use client";

import { useQuery } from "@tanstack/react-query";
import { UserProjectsResponseSchema } from "@/types";

export function useUserProjects() {
  return useQuery({
    queryKey: ["user-projects"],
    queryFn: async () => {
      const res = await fetch("/api/projects");
      if (!res.ok) {
        throw new Error("Failed to load projects");
      }
      return UserProjectsResponseSchema.parse(await res.json());
    },
  });
}
