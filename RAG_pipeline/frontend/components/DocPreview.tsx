"use client";

import { useEffect, useState } from "react";
import { X, Download, Loader2 } from "lucide-react";
import { MarkdownContent } from "@/components/MarkdownContent";

interface DocPreviewProps {
  projectId: string;
  filename: string;
  /** Sub-project the doc belongs to; null for a flat project. */
  subproject: string | null;
  hasPdf: boolean;
  onClose: () => void;
}

export function DocPreview({
  projectId,
  filename,
  subproject,
  hasPdf,
  onClose,
}: DocPreviewProps) {
  const [content, setContent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Every sub-project of a partitioned project holds the same doc names, so the
  // sub-project has to travel with the request or the backend cannot tell which
  // file is meant.
  const query = subproject
    ? `?subproject=${encodeURIComponent(subproject)}`
    : "";
  const href = `/api/docs/${projectId}/${encodeURIComponent(filename)}${query}`;
  const pdfHref = `/api/docs/${projectId}/${encodeURIComponent(
    filename.replace(/\.md$/, ".pdf"),
  )}${query}`;

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setContent(null);

    fetch(href)
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`Failed to load: ${res.statusText}`);
        }
        return res.text();
      })
      .then((text) => {
        if (!cancelled) {
          setContent(text);
          setIsLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setIsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [href]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b bg-card">
        <h3 className="text-sm font-medium truncate">
          {subproject && (
            <span className="text-muted-foreground">{subproject} / </span>
          )}
          {filename}
        </h3>
        <div className="flex items-center gap-2 shrink-0">
          <a
            href={href}
            download
            className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border hover:bg-muted transition-colors"
          >
            <Download className="h-3 w-3" />
            MD
          </a>
          {hasPdf && (
            <a
              href={pdfHref}
              download
              className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              <Download className="h-3 w-3" />
              PDF
            </a>
          )}
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-muted transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading document...
          </div>
        )}
        {error && (
          <p className="text-sm text-destructive">Error: {error}</p>
        )}
        {content && <MarkdownContent content={content} />}
      </div>
    </div>
  );
}
