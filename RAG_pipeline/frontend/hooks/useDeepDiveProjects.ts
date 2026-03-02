"use client";

import { useQuery } from "@tanstack/react-query";
import type { DeepDiveProjectsResponse } from "@/types";

async function fetchDeepDiveProjects(): Promise<DeepDiveProjectsResponse> {
  const res = await fetch("/api/deep-dive/projects");
  if (!res.ok) {
    const text = await res.text();
    let message = "Failed to fetch projects";
    try {
      message = JSON.parse(text).error || message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return res.json();
}

export function useDeepDiveProjects() {
  return useQuery({
    queryKey: ["deep-dive-projects"],
    queryFn: fetchDeepDiveProjects,
  });
}
