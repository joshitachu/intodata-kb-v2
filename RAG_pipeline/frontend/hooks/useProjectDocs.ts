"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
import type { DocListResponse, PdfGenerateResponse } from "@/types";

export function useProjectDocs(projectId: string) {
  return useQuery({
    queryKey: ["project-docs", projectId],
    queryFn: () =>
      fetchJson<DocListResponse>(`/api/docs/${encodeURIComponent(projectId)}`, {
        fallbackError: "Failed to fetch docs",
      }),
    enabled: !!projectId,
  });
}

export function useGeneratePdfs(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () =>
      fetchJson<PdfGenerateResponse>(
        `/api/docs/${encodeURIComponent(projectId)}/generate-pdfs`,
        { method: "POST", fallbackError: "Failed to generate PDFs" },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project-docs", projectId] });
    },
  });
}
