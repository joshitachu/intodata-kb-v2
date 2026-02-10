"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";
import type { DocumentSource } from "@/types";
import { cn } from "@/lib/utils";

interface SourceCitationProps {
  source: DocumentSource;
  index: number;
}

export function SourceCitation({ source, index }: SourceCitationProps) {
  const [expanded, setExpanded] = useState(false);
  const displayNumber = source.reference_number;

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
        <span className="font-medium truncate">{source.filename}</span>
        <span className="text-muted-foreground ml-auto shrink-0">
          {(source.score * 100).toFixed(0)}% match
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-3 pt-1 border-t">
          <p className="text-sm text-muted-foreground whitespace-pre-wrap">
            {source.text}
          </p>
        </div>
      )}
    </div>
  );
}
