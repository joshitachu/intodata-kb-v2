"use client";

import { Eye, Download, FileText, Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useProjectDocs, useGeneratePdfs } from "@/hooks/useProjectDocs";
import type { DocFileInfo } from "@/types";

interface DocListProps {
  projectId: string;
  selectedDoc: string | null;
  onSelectDoc: (filename: string) => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function DocList({ projectId, selectedDoc, onSelectDoc }: DocListProps) {
  const { data, isLoading, error } = useProjectDocs(projectId);
  const generatePdfs = useGeneratePdfs(projectId);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading docs...
      </div>
    );
  }

  if (error) {
    return (
      <p className="p-4 text-sm text-destructive">
        Failed to load docs: {error.message}
      </p>
    );
  }

  if (!data || data.docs.length === 0) {
    return (
      <p className="p-4 text-sm text-muted-foreground">
        No documentation files found for this project.
      </p>
    );
  }

  const hasPdfSupport = data.docs.some((d) => d.has_pdf);
  const allHavePdf = data.docs.every((d) => d.has_pdf);

  return (
    <div className="flex flex-col h-full">
      {/* Generate PDFs button */}
      <div className="p-3 border-b">
        <button
          onClick={() => generatePdfs.mutate()}
          disabled={generatePdfs.isPending}
          className={cn(
            "w-full flex items-center justify-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors",
            "bg-primary text-primary-foreground hover:bg-primary/90",
            "disabled:opacity-50 disabled:cursor-not-allowed",
          )}
        >
          {generatePdfs.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Generating PDFs...
            </>
          ) : (
            <>
              <FileText className="h-4 w-4" />
              {allHavePdf ? "Regenerate PDFs" : "Generate PDFs"}
            </>
          )}
        </button>
        {generatePdfs.data && (
          <p className="mt-2 text-xs text-muted-foreground">
            {generatePdfs.data.converted} converted
            {generatePdfs.data.failed > 0 &&
              `, ${generatePdfs.data.failed} failed`}
          </p>
        )}
        {generatePdfs.error && (
          <p className="mt-2 text-xs text-destructive">
            {generatePdfs.error.message}
          </p>
        )}
      </div>

      {/* Doc list */}
      <div className="flex-1 overflow-y-auto">
        {data.docs.map((doc: DocFileInfo) => (
          <div
            key={doc.name}
            className={cn(
              "flex items-center gap-2 px-3 py-2 border-b cursor-pointer transition-colors",
              selectedDoc === doc.name
                ? "bg-secondary"
                : "hover:bg-muted/50",
            )}
            onClick={() => onSelectDoc(doc.name)}
          >
            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="flex-1 min-w-0">
              <p className="text-sm truncate font-medium">{doc.name}</p>
              <p className="text-xs text-muted-foreground">
                {formatSize(doc.size_bytes)}
                {doc.has_pdf && (
                  <span className="ml-2 inline-flex items-center gap-1 text-[#a0e06f]">
                    <CheckCircle className="h-3 w-3" />
                    PDF
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectDoc(doc.name);
                }}
                className="p-1 rounded hover:bg-muted"
                title="Preview"
              >
                <Eye className="h-4 w-4 text-muted-foreground" />
              </button>
              <a
                href={`/api/docs/${projectId}/${doc.name}`}
                download
                onClick={(e) => e.stopPropagation()}
                className="p-1 rounded hover:bg-muted"
                title="Download MD"
              >
                <Download className="h-4 w-4 text-muted-foreground" />
              </a>
              {doc.has_pdf && (
                <a
                  href={`/api/docs/${projectId}/${doc.name.replace(/\.md$/, ".pdf")}`}
                  download
                  onClick={(e) => e.stopPropagation()}
                  className="p-1 rounded hover:bg-muted"
                  title="Download PDF"
                >
                  <Download className="h-4 w-4 text-primary" />
                </a>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="p-2 border-t text-xs text-muted-foreground text-center">
        {data.total} document{data.total !== 1 ? "s" : ""}
        {hasPdfSupport &&
          ` | ${data.docs.filter((d) => d.has_pdf).length} PDFs`}
      </div>
    </div>
  );
}
