"use client";

import { useState } from "react";
import { Eye, Download, FileText, Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useProjectDocs, useGeneratePdfs } from "@/hooks/useProjectDocs";
import { docKey, type DocFileInfo } from "@/types";

interface DocListProps {
  projectId: string;
  selectedDoc: DocFileInfo | null;
  onSelectDoc: (doc: DocFileInfo) => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Download href for a doc, carrying its sub-project when it has one. */
function docHref(projectId: string, doc: DocFileInfo, name: string): string {
  const query = doc.subproject
    ? `?subproject=${encodeURIComponent(doc.subproject)}`
    : "";
  return `/api/docs/${projectId}/${encodeURIComponent(name)}${query}`;
}

export function DocList({ projectId, selectedDoc, onSelectDoc }: DocListProps) {
  const { data, isLoading, error } = useProjectDocs(projectId);
  const generatePdfs = useGeneratePdfs(projectId);
  // null = "All".
  const [subprojectFilter, setSubprojectFilter] = useState<string | null>(null);

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

  const subprojects = data.subprojects;
  // Derive the active filter rather than resetting it in an effect: this
  // component is not remounted when the selected project changes, so a filter
  // left over from the previous project would otherwise hide every document
  // with no visible cause.
  const activeSubproject =
    subprojectFilter && subprojects.includes(subprojectFilter)
      ? subprojectFilter
      : null;
  const visibleDocs = activeSubproject
    ? data.docs.filter((d) => d.subproject === activeSubproject)
    : data.docs;

  const hasPdfSupport = visibleDocs.some((d) => d.has_pdf);
  const allHavePdf = visibleDocs.every((d) => d.has_pdf);
  const selectedKey = selectedDoc ? docKey(selectedDoc) : null;

  return (
    <div className="flex flex-col h-full">
      {/* Generate PDFs button */}
      <div className="p-3 border-b" data-tour="docs-doc-list">
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

      {/* Sub-project filter -- only rendered when the project has sub-projects */}
      {subprojects.length > 0 && (
        <div
          className="flex flex-wrap gap-1 p-2 border-b"
          role="group"
          aria-label="Filter by sub-project"
        >
          <button
            onClick={() => setSubprojectFilter(null)}
            aria-pressed={activeSubproject === null}
            className={cn(
              "px-2 py-1 rounded-full text-xs border transition-colors",
              activeSubproject === null
                ? "bg-primary text-primary-foreground border-primary"
                : "hover:bg-muted",
            )}
          >
            All ({data.docs.length})
          </button>
          {subprojects.map((sub) => {
            const count = data.docs.filter((d) => d.subproject === sub).length;
            return (
              <button
                key={sub}
                onClick={() => setSubprojectFilter(sub)}
                aria-pressed={activeSubproject === sub}
                title={sub}
                className={cn(
                  "px-2 py-1 rounded-full text-xs border transition-colors max-w-[12rem] truncate",
                  activeSubproject === sub
                    ? "bg-primary text-primary-foreground border-primary"
                    : "hover:bg-muted",
                )}
              >
                {sub} ({count})
              </button>
            );
          })}
        </div>
      )}

      {/* Doc list */}
      <div className="flex-1 overflow-y-auto">
        {visibleDocs.map((doc: DocFileInfo) => (
          <div
            key={docKey(doc)}
            className={cn(
              "flex items-center gap-2 px-3 py-2 border-b cursor-pointer transition-colors",
              selectedKey === docKey(doc)
                ? "bg-secondary"
                : "hover:bg-muted/50",
            )}
            onClick={() => onSelectDoc(doc)}
          >
            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="flex-1 min-w-0">
              <p className="text-sm truncate font-medium">{doc.name}</p>
              <p className="text-xs text-muted-foreground">
                {/* Under "All" every sub-project contributes the same ten doc
                    names, so the name alone does not say which one this is. */}
                {doc.subproject && activeSubproject === null && (
                  <span className="mr-2 font-medium text-foreground/70">
                    {doc.subproject}
                  </span>
                )}
                {formatSize(doc.size_bytes)}
                {doc.has_pdf && (
                  <span className="ml-2 inline-flex items-center gap-1 text-green-600">
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
                  onSelectDoc(doc);
                }}
                className="p-1 rounded hover:bg-muted"
                title="Preview"
              >
                <Eye className="h-4 w-4 text-muted-foreground" />
              </button>
              <a
                href={docHref(projectId, doc, doc.name)}
                download
                onClick={(e) => e.stopPropagation()}
                className="p-1 rounded hover:bg-muted"
                title="Download MD"
              >
                <Download className="h-4 w-4 text-muted-foreground" />
              </a>
              {doc.has_pdf && (
                <a
                  href={docHref(projectId, doc, doc.name.replace(/\.md$/, ".pdf"))}
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
        {visibleDocs.length} document{visibleDocs.length !== 1 ? "s" : ""}
        {activeSubproject && ` in ${activeSubproject} of ${data.total}`}
        {hasPdfSupport &&
          ` | ${visibleDocs.filter((d) => d.has_pdf).length} PDFs`}
      </div>
    </div>
  );
}
