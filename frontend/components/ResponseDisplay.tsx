"use client";

import type { QueryResponse } from "@/types";
import { SourceCitation } from "./SourceCitation";
import { LoadingMessages } from "./LoadingMessages";
import { MarkdownContent } from "./MarkdownContent";

interface ResponseDisplayProps {
  response: QueryResponse | null;
  isLoading: boolean;
  error: Error | null;
}

export function ResponseDisplay({ response, isLoading, error }: ResponseDisplayProps) {
  if (isLoading) {
    return (
      <div className="w-full max-w-2xl mx-auto mt-8">
        <LoadingMessages />
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full max-w-2xl mx-auto mt-8">
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
          <p className="text-sm text-destructive">{error.message}</p>
        </div>
      </div>
    );
  }

  if (!response) return null;

  return (
    <div className="w-full max-w-2xl mx-auto mt-8 space-y-6">
      {/* Answer */}
      <MarkdownContent content={response.answer} />

      {/* Metadata */}
      <p className="text-xs text-muted-foreground">
        {response.sources.length > 0 && (
          <>{response.sources.length} source{response.sources.length !== 1 ? "s" : ""} &middot; </>
        )}
        {response.latency_ms}ms
      </p>

      {/* Sources */}
      {response.sources.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Sources</h3>
          {response.sources.map((source, i) => (
            <SourceCitation key={source.document_id} source={source} index={i} />
          ))}
        </div>
      )}
    </div>
  );
}
