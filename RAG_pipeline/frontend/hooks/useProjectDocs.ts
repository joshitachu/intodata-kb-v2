"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { DocListResponse, PdfGenerateResponse } from "@/types";

async function fetchProjectDocs(projectId: string): Promise<DocListResponse> {
  const res = await fetch(`/api/docs/${projectId}`);
  if (!res.ok) {
    const text = await res.text();
    let message = "Failed to fetch docs";
    try {
      message = JSON.parse(text).error || message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return res.json();
}

async function generatePdfs(projectId: string): Promise<PdfGenerateResponse> {
  const res = await fetch(`/api/docs/${projectId}/generate-pdfs`, {
    method: "POST",
  });
  if (!res.ok) {
    const text = await res.text();
    let message = "Failed to generate PDFs";
    try {
      message = JSON.parse(text).error || message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return res.json();
}

export function useProjectDocs(projectId: string) {
  return useQuery({
    queryKey: ["project-docs", projectId],
    queryFn: () => fetchProjectDocs(projectId),
    enabled: !!projectId,
  });
}

export function useGeneratePdfs(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => generatePdfs(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project-docs", projectId] });
    },
  });
}
