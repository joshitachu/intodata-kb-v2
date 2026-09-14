"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
import type { DocumentListResponse, DocumentRole, IngestResponse } from "@/types";

async function uploadDocument({
  file,
  project_id,
  document_role,
}: {
  file: File;
  project_id: string;
  document_role: DocumentRole;
}): Promise<IngestResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("project_id", project_id);
  formData.append("document_role", document_role);

  return fetchJson<IngestResponse>("/api/ingest", {
    method: "POST",
    body: formData,
    fallbackError: "Upload failed",
  });
}

export function useDocuments() {
  return useQuery({
    queryKey: ["documents"],
    queryFn: () =>
      fetchJson<DocumentListResponse>("/api/documents", {
        fallbackError: "Failed to fetch documents",
      }),
  });
}

export function useDeleteDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      fetchJson<void>(`/api/documents?id=${id}`, {
        method: "DELETE",
        fallbackError: "Delete failed",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useUploadDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadDocument,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["docs-projects"] });
      queryClient.invalidateQueries({ queryKey: ["user-projects"] });
    },
  });
}
