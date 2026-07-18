import { X } from "lucide-react";
import {
  confidenceStyle,
  summarizeLineage,
  type LineageSummary,
} from "@/lib/lineageHighlight";
import type { LineageFieldResponse } from "@/types/lineage";

interface LineagePathBannerProps {
  response: LineageFieldResponse | null;
  isLoading: boolean;
  error: unknown;
  onClear: () => void;
}

// Floating summary/legend for the active field-lineage trace (S5,
// "heritage mode"). Rendered once at the topology page level so it stays
// visible across whichever level (project/job/component) the highlight
// currently applies to - the canvases themselves only render the
// dim/highlight overlay on their own nodes/edges.
export function LineagePathBanner({
  response,
  isLoading,
  error,
  onClear,
}: LineagePathBannerProps) {
  if (!isLoading && !error && !response) return null;

  const summary: LineageSummary | null = response ? summarizeLineage(response) : null;

  return (
    <div
      className="absolute top-3 left-1/2 -translate-x-1/2 z-30 rounded-lg border bg-card/95 backdrop-blur-sm shadow-lg px-3 py-2 text-xs max-w-[90vw]"
      data-testid="lineage-path-banner"
    >
      <div className="flex items-center gap-3">
        {isLoading && (
          <span className="text-muted-foreground">Tracing field lineage...</span>
        )}
        {!isLoading && Boolean(error) && (
          <span className="text-destructive" data-testid="lineage-banner-error">
            {error instanceof Error ? error.message : "Failed to load field lineage"}
          </span>
        )}
        {!isLoading && !error && summary && (
          <>
            <span className="font-semibold font-mono">
              {summary.startTable}.{summary.startColumn}
            </span>
            <span className="text-muted-foreground">
              {summary.hopCount} hop{summary.hopCount === 1 ? "" : "s"}
            </span>
            <div className="flex items-center gap-1.5">
              {Object.entries(summary.confidenceCounts).map(([confidence, count]) => {
                const style = confidenceStyle(confidence);
                return (
                  <span
                    key={confidence}
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-white font-medium"
                    style={{ background: style.color }}
                    title={style.label}
                    data-testid={`lineage-confidence-badge-${confidence}`}
                  >
                    {confidence} &times;{count}
                  </span>
                );
              })}
            </div>
            {summary.dynamicRefCount > 0 && (
              <span className="text-amber-600" data-testid="lineage-dynamic-refs">
                {summary.dynamicRefCount} unresolved dynamic ref
                {summary.dynamicRefCount === 1 ? "" : "s"}
              </span>
            )}
            {summary.truncated && (
              <span className="text-muted-foreground italic">truncated at max depth</span>
            )}
          </>
        )}
        <button
          onClick={onClear}
          className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground shrink-0"
          title="Clear lineage trace"
          aria-label="Clear lineage trace"
          data-testid="lineage-banner-close"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
