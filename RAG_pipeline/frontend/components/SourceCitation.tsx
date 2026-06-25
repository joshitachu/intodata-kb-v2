"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FileText, Hash } from "lucide-react";
import type { DocumentSource } from "@/types";
import { cn } from "@/lib/utils";

interface SourceCitationProps {
  source: DocumentSource;
  index: number;
}

const ROLE_LABELS: Record<DocumentSource["document_role"], string> = {
  source: "Source",
  reference: "Reference",
  guidance: "Guidance",
  standard: "Standard",
  template: "Template",
  policy: "Policy",
};

export function SourceCitation({ source, index }: SourceCitationProps) {
  const [expanded, setExpanded] = useState(false);
  const displayNumber = source.reference_number;
  const snippet = source.text.replace(/\s+/g, " ").trim().slice(0, 220);
  const chunkLabel =
    source.chunk_indexes && source.chunk_indexes.length > 0
      ? `Chunks ${source.chunk_indexes.join(", ")}`
      : "Indexed source";

  return (
    <div
      id={`source-${displayNumber}`}
      className="border rounded-md scroll-mt-4"
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className={cn(
          "flex items-center gap-2 w-full px-3 py-2 text-left text-sm",
          "hover:bg-muted/50 transition-colors",
        )}
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        <span className="inline-flex items-center justify-center h-5 w-5 rounded-full bg-primary text-primary-foreground text-xs font-medium shrink-0">
          {displayNumber}
        </span>
        <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
        <span className="font-medium truncate">{source.display_name ?? source.filename}</span>
        {source.document_role !== "source" && (
          <span className="rounded border border-border bg-muted/50 px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground shrink-0">
            {ROLE_LABELS[source.document_role]}
          </span>
        )}
        <span className="text-muted-foreground ml-auto shrink-0">
          {(source.score * 100).toFixed(0)}% match
        </span>
      </button>
      <div className="px-3 pb-2 -mt-1 flex items-start gap-2 text-xs text-muted-foreground">
        <Hash className="h-3 w-3 mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="font-medium">{chunkLabel}</div>
          {!expanded && snippet && (
            <div className="truncate">{snippet}{source.text.length > snippet.length ? "..." : ""}</div>
          )}
        </div>
      </div>
      {expanded && (
        <div className="px-3 pb-3 pt-1 border-t">
          <p className="text-sm text-muted-foreground whitespace-pre-wrap leading-relaxed">
            {source.text}
          </p>
        </div>
      )}
    </div>
  );
}
