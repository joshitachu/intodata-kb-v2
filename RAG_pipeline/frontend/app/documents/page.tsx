"use client";

import { useState } from "react";
import { FileText, Trash2, Loader2 } from "lucide-react";
import { DocumentUpload } from "@/components/DocumentUpload";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useDocuments, useDeleteDocument } from "@/hooks/useDocuments";
import type { DocumentRole } from "@/types";

const ROLE_LABELS: Record<DocumentRole, string> = {
  source: "Source",
  reference: "Reference",
  guidance: "Guidance",
  standard: "Standard",
  template: "Template",
  policy: "Policy",
};

export default function DocumentsPage() {
  const { data, isLoading, error } = useDocuments();
  const deleteDoc = useDeleteDocument();
  const [deleteTarget, setDeleteTarget] = useState<{ id: number; filename: string } | null>(null);

  function handleDeleteConfirmed() {
    if (!deleteTarget) return;
    deleteDoc.mutate(deleteTarget.id);
    setDeleteTarget(null);
  }

  return (
    <div className="max-w-4xl mx-auto py-8" data-tour="documents-page">
      <h1 className="text-2xl font-bold mb-6">Documents</h1>

      <div className="mb-8">
        <h2 className="text-lg font-medium mb-4">Upload New Document</h2>
        <DocumentUpload />
      </div>

      <div>
        <h2 className="text-lg font-medium mb-4">
          My Documents
          {data && <span className="text-muted-foreground font-normal"> ({data.total})</span>}
        </h2>

        {isLoading && (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading documents...
          </div>
        )}

        {error && (
          <div className="text-destructive text-sm">
            Failed to load documents: {error.message}
          </div>
        )}

        {data && data.documents.length === 0 && (
          <p className="text-muted-foreground text-sm">
            No documents yet. Upload a file to get started.
          </p>
        )}

        {data && data.documents.length > 0 && (
          <div className="border rounded-lg divide-y">
            {data.documents.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50"
              >
                <FileText className="h-5 w-5 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{doc.filename}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span>
                      {doc.file_type.toUpperCase()} &middot; {doc.chunk_count} chunks
                      {doc.author && ` \u00b7 ${doc.author}`}
                    </span>
                    <span className="rounded border border-border bg-muted/50 px-1.5 py-0.5 text-[11px] font-medium text-foreground">
                      {ROLE_LABELS[doc.document_role]}
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => setDeleteTarget({ id: doc.id, filename: doc.filename })}
                  disabled={deleteDoc.isPending}
                  className="p-2 text-muted-foreground hover:text-destructive disabled:opacity-50"
                  title="Delete document"
                >
                  {deleteDoc.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Document"
        message={`Are you sure you want to delete "${deleteTarget?.filename}"? This will also remove all associated vector embeddings and cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={handleDeleteConfirmed}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
