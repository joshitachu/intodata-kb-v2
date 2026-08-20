"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useRuntimeExecutionLogs } from "@/hooks/useRuntimeLogs";
import type { RuntimeLogEvent, RuntimeLogSeverity } from "@/types";
import { cn } from "@/lib/utils";
import { SeverityChip, formatDate } from "./runtime-ui";

const LOG_PAGE_SIZE = 100;

// Severity is a floor: the server returns this level and everything above it.
const SEVERITY_OPTIONS: Array<{ value: "" | RuntimeLogSeverity; label: string }> = [
  { value: "", label: "All severities" },
  { value: "debug", label: "Debug and above" },
  { value: "info", label: "Info and above" },
  { value: "warning", label: "Warnings and errors" },
  { value: "error", label: "Errors only" },
];

/** A message is collapsed by default when it is tall enough to bury the rows below it. */
const COLLAPSE_LINE_THRESHOLD = 4;

export function RuntimeLogViewer({ executionId }: { executionId: string }) {
  const [minSeverity, setMinSeverity] = useState<"" | RuntimeLogSeverity>("");
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    setPage(0);
  }, [executionId, minSeverity]);

  const query = useRuntimeExecutionLogs(executionId, {
    minSeverity: minSeverity || undefined,
    limit: LOG_PAGE_SIZE,
    offset: page * LOG_PAGE_SIZE,
  });

  const events = query.data?.events ?? [];
  const total = query.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / LOG_PAGE_SIZE));

  function toggle(key: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <section className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Log lines
        </h3>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="sr-only">Minimum severity</span>
          <select
            value={minSeverity}
            onChange={(event) => setMinSeverity(event.target.value as "" | RuntimeLogSeverity)}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground"
          >
            {SEVERITY_OPTIONS.map((option) => (
              <option key={option.value || "all"} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {query.isLoading && (
        <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
          Loading log lines
        </p>
      )}

      {query.isError && (
        <div className="border border-destructive/40 p-3 text-sm">
          <p className="text-destructive">Log lines could not be loaded.</p>
          <button
            type="button"
            onClick={() => query.refetch()}
            className="mt-2 rounded-md border border-input px-2 py-1 text-xs"
          >
            Retry
          </button>
        </div>
      )}

      {!query.isLoading && !query.isError && events.length === 0 && (
        <p className="py-6 text-sm text-muted-foreground">
          {total === 0 && !minSeverity
            ? "No log lines were captured for this run. Logs are only retained for the configured window, and successful runs may not have been captured."
            : "No log lines match this severity filter."}
        </p>
      )}

      {events.length > 0 && (
        <>
          <p className="text-xs text-muted-foreground">
            {total.toLocaleString()} line{total === 1 ? "" : "s"}
            {pages > 1 && ` · page ${page + 1} of ${pages}`}
          </p>
          <ol className="min-w-0 divide-y divide-border border border-border">
            {events.map((event) => (
              <LogLine
                // Position is part of a line's identity: repeated messages are valid data.
                key={`${event.execution_id}:${event.line_ordinal}`}
                event={event}
                isExpanded={expanded.has(`${event.execution_id}:${event.line_ordinal}`)}
                onToggle={() => toggle(`${event.execution_id}:${event.line_ordinal}`)}
              />
            ))}
          </ol>
          {pages > 1 && (
            <div className="flex items-center justify-between gap-3 text-xs">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
                className="rounded-md border border-input px-2 py-1 disabled:opacity-50"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={page + 1 >= pages}
                onClick={() => setPage(page + 1)}
                className="rounded-md border border-input px-2 py-1 disabled:opacity-50"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function LogLine({
  event,
  isExpanded,
  onToggle,
}: {
  event: RuntimeLogEvent;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const lineCount = event.message.split("\n").length;
  const isTall = lineCount > COLLAPSE_LINE_THRESHOLD || event.message.length > 400;

  return (
    <li className="min-w-0 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityChip severity={event.severity} />
        <span className="text-[11px] tabular-nums text-muted-foreground">
          {formatDate(event.timestamp)}
        </span>
        {isTall && (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={isExpanded}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-input px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
          >
            {isExpanded ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            {isExpanded ? "Collapse" : `Show all ${lineCount} lines`}
          </button>
        )}
      </div>
      {/* Stack traces carry newlines and tabs: preserve them, wrap long tokens, and
          never let the pre block widen the drawer. */}
      <pre
        className={cn(
          "mt-1 min-w-0 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-foreground",
          isTall && !isExpanded && "line-clamp-3",
        )}
      >
        {event.message}
      </pre>
    </li>
  );
}
