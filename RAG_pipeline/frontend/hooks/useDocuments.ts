"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { DocumentListResponse, DocumentRole, IngestResponse } from "@/types";

async function fetchDocuments(): Promise<DocumentListResponse> {
  const res = await fetch("/api/documents");
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.error || "Failed to fetch documents");
  }
  return res.json();
}

async function deleteDocument(id: number): Promise<void> {
  const res = await fetch(`/api/documents?id=${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    const data = await res.json();
    throw new Error(data.error || "Delete failed");
  }
}

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

  const res = await fetch("/api/ingest", {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.error || "Upload failed");
  }

  return res.json();
}

export function useDocuments() {
  return useQuery({
    queryKey: ["documents"],
    queryFn: fetchDocuments,
  });
}

export function useDeleteDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteDocument,
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
