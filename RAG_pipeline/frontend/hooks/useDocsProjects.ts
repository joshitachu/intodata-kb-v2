"use client";

import { useQuery } from "@tanstack/react-query";
import type { DocsProjectsResponse } from "@/types";

async function fetchDocsProjects(): Promise<DocsProjectsResponse> {
  const res = await fetch("/api/docs/projects");
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

export function useDocsProjects() {
  return useQuery({
    queryKey: ["docs-projects"],
    queryFn: fetchDocsProjects,
  });
}
