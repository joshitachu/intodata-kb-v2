"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Loader2, Terminal } from "lucide-react";
import { useRuntimeExecutionLogs } from "@/hooks/useRuntimeLogs";
import { Button } from "@/components/ui/button";
import { SelectMenu } from "@/components/ui/select-menu";
import type { RuntimeLogEvent, RuntimeLogSeverity } from "@/types";
import { cn } from "@/lib/utils";
import { SeverityChip, formatClock, formatDate } from "./runtime-ui";

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
    <section className="flex min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-elevation-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 px-4 py-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Terminal className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          Log lines
          {events.length > 0 && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
              {total.toLocaleString()}
              {pages > 1 && ` · page ${page + 1}/${pages}`}
            </span>
          )}
        </h3>
        <SelectMenu
          value={minSeverity}
          onChange={(value) => setMinSeverity(value as "" | RuntimeLogSeverity)}
          options={SEVERITY_OPTIONS.map((option) => ({
            value: option.value,
            label: option.label,
          }))}
          placeholder="All severities"
          ariaLabel="Minimum severity"
          variant="field"
          align="end"
        />
      </div>

      {query.isLoading && (
        <p className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
          Loading log lines
        </p>
      )}

      {query.isError && (
        <div className="m-4 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
          <p className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
            Log lines could not be loaded.
          </p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-2"
            onClick={() => query.refetch()}
          >
            Retry
          </Button>
        </div>
      )}

      {!query.isLoading && !query.isError && events.length === 0 && (
        <p className="px-4 py-8 text-center text-sm text-muted-foreground">
          {total === 0 && !minSeverity
            ? "No log lines were captured for this run. Logs are only retained for the configured window, and successful runs may not have been captured."
            : "No log lines match this severity filter."}
        </p>
      )}

      {events.length > 0 && (
        <>
          {/* Dark gutter on both themes: a log stream reads better against a
              constant backdrop than one that inverts with the app theme. */}
          <ol className="min-w-0 divide-y divide-border/60 bg-muted/20">
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
            <div className="flex items-center justify-between gap-3 border-t border-border/60 px-4 py-2.5">
              <Button
                variant="secondary"
                size="sm"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                Previous
              </Button>
              <span className="text-xs tabular-nums text-muted-foreground">
                Page {page + 1} of {pages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page + 1 >= pages}
                onClick={() => setPage(page + 1)}
              >
                Next
              </Button>
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
    <li
      className={cn(
        "min-w-0 px-4 py-2 transition-colors hover:bg-muted/40 motion-reduce:transition-none",
        // An error line is the reason someone opened this drawer: give it a rail.
        event.severity === "error" && "border-l-2 border-l-destructive bg-destructive/[0.04]",
        event.severity === "warning" && "border-l-2 border-l-amber-500/60",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <SeverityChip severity={event.severity} />
        <span
          className="font-mono text-[11px] tabular-nums text-muted-foreground"
          title={formatDate(event.timestamp)}
        >
          {formatClock(event.timestamp)}
        </span>
        {isTall && (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={isExpanded}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
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
          "mt-1 min-w-0 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed",
          event.severity === "error" ? "text-foreground" : "text-foreground/90",
          isTall && !isExpanded && "line-clamp-3",
        )}
      >
        {event.message}
      </pre>
    </li>
  );
}
