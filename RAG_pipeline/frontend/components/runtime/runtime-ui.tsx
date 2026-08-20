"use client";

import type { ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cog,
  HelpCircle,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";

// Shared chrome + formatters for the runtime console. Extracted from
// RuntimeConsole so the aggregate panels render identical panels, tables and
// pagination instead of each re-inventing them.

export const PAGE_SIZE = 50;

export function Panel({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="border border-border bg-card">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-4 py-3">
        <div>
          <h2 className="font-semibold">{title}</h2>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

export type KpiTone = "neutral" | "good" | "warning" | "danger";

const KPI_TONES: Record<KpiTone, string> = {
  neutral: "text-primary",
  good: "text-emerald-600 dark:text-emerald-400",
  warning: "text-amber-600 dark:text-amber-400",
  danger: "text-destructive",
};

/**
 * Stat tile: label, value, optional hint. The tone icon is the secondary
 * encoding that keeps state legible without relying on color alone.
 */
export function Kpi({
  icon,
  label,
  value,
  hint,
  tone = "neutral",
}: {
  icon: ReactNode;
  label: string;
  value: string;
  hint?: string;
  tone?: KpiTone;
}) {
  return (
    <div className="border border-border bg-card p-4">
      <div className={cn("flex items-center gap-2", KPI_TONES[tone])}>
        {icon}
        <span className="text-sm font-medium text-muted-foreground">{label}</span>
      </div>
      <p className="mt-3 text-2xl font-semibold text-foreground">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function Empty({ label }: { label: string }) {
  return <p className="py-8 text-center text-sm text-muted-foreground">{label}</p>;
}

type QueryLike = { isLoading: boolean; isError: boolean; refetch: () => unknown };

export function QueryState({
  queries,
  children,
}: {
  queries: QueryLike[];
  children: ReactNode;
}) {
  if (queries.some((query) => query.isLoading)) {
    return (
      <div className="flex min-h-48 items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
        Loading runtime data
      </div>
    );
  }
  if (queries.some((query) => query.isError)) {
    return (
      <div className="border border-destructive/40 p-5">
        <p className="font-medium text-destructive">Some runtime data could not be loaded.</p>
        <button
          onClick={() => queries.forEach((query) => query.refetch())}
          className="mt-3 inline-flex items-center gap-2 rounded-md border border-input px-3 py-2 text-sm"
        >
          <RefreshCw className="h-4 w-4" />
          Retry
        </button>
      </div>
    );
  }
  return <>{children}</>;
}

export function EntityTable<T>({
  headers,
  rows,
  rowKey,
  onClick,
  cells,
  numeric,
  emptyLabel = "No records are available for this scope.",
}: {
  headers: string[];
  rows: T[];
  rowKey: (item: T) => string;
  onClick?: (item: T) => void;
  cells: (item: T) => ReactNode[];
  /** Column indexes rendered right-aligned with tabular figures. */
  numeric?: number[];
  emptyLabel?: string;
}) {
  if (rows.length === 0) return <Empty label={emptyLabel} />;
  const numericSet = new Set(numeric ?? []);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[620px] text-left text-sm">
        <thead className="border-b border-border text-xs uppercase text-muted-foreground">
          <tr>
            {headers.map((header, index) => (
              <th
                key={header}
                className={cn("px-3 py-3 font-medium", numericSet.has(index) && "text-right")}
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr
              key={rowKey(item)}
              tabIndex={onClick ? 0 : undefined}
              onClick={onClick ? () => onClick(item) : undefined}
              onKeyDown={
                onClick
                  ? (event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onClick(item);
                      }
                    }
                  : undefined
              }
              className={cn(
                "border-b border-border/70 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none",
                onClick && "cursor-pointer hover:bg-muted/60",
              )}
            >
              {cells(item).map((cell, index) => (
                <td
                  key={`${rowKey(item)}:${headers[index]}`}
                  className={cn(
                    "max-w-64 truncate px-3 py-3",
                    index === 0 && "font-medium text-foreground",
                    numericSet.has(index) && "text-right tabular-nums",
                  )}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PaginatedTable<T>({
  rows,
  total,
  page,
  onPage,
  render,
}: {
  rows: T[];
  total: number;
  page: number;
  onPage: (page: number) => void;
  render: (rows: T[]) => ReactNode;
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <>
      <p className="mb-3 text-sm text-muted-foreground">{total} records</p>
      {render(rows)}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm">
        <span className="text-muted-foreground">
          Page {page + 1} of {pages}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={page === 0}
            onClick={() => onPage(page - 1)}
            className="rounded-md border border-input px-3 py-1.5 disabled:opacity-50"
          >
            Previous
          </button>
          <button
            type="button"
            disabled={page + 1 >= pages}
            onClick={() => onPage(page + 1)}
            className="rounded-md border border-input px-3 py-1.5 disabled:opacity-50"
          >
            Next
          </button>
        </div>
      </div>
    </>
  );
}

const SEVERITY_STYLES: Record<string, string> = {
  error: "bg-destructive/10 text-destructive",
  warning: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  info: "bg-muted text-muted-foreground",
  debug: "bg-muted/60 text-muted-foreground",
};

export function SeverityChip({ severity }: { severity: string }) {
  return (
    <span
      className={cn(
        "inline-block rounded px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide",
        SEVERITY_STYLES[severity] ?? "bg-muted text-muted-foreground",
      )}
    >
      {severity}
    </span>
  );
}

// --- cause provenance -------------------------------------------------------

/**
 * Confidence is state: icon + label, never colour alone.
 *
 * "low" is the catalog's fallback tier — it means no cause was derived, which is
 * the majority of real incidents. It must not read as analysis.
 */
export function ConfidenceBadge({ confidence }: { confidence?: string | null }) {
  if (!confidence) return <span className="text-muted-foreground">-</span>;
  const tone =
    confidence === "high"
      ? "text-emerald-600 dark:text-emerald-400"
      : confidence === "medium"
        ? "text-amber-600 dark:text-amber-400"
        : "text-muted-foreground";
  const Icon =
    confidence === "high" ? CheckCircle2 : confidence === "medium" ? AlertTriangle : HelpCircle;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs", tone)}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {confidence === "low" ? "low — not a diagnosis" : confidence}
    </span>
  );
}

/**
 * Who wrote the cause.
 *
 * The treatment is deliberately asymmetric. `deterministic` is the expected
 * value for every incident today, so a loud badge on all of them would be pure
 * noise — it states provenance quietly. `llm_fallback` means a language model
 * inferred the cause rather than a rule matching it, so it is marked
 * unmistakably: the fallback is disabled by default, and any such value in
 * future data is the exception a reader must not miss.
 */
export function CauseOriginBadge({ origin }: { origin?: string | null }) {
  if (!origin) return null;

  if (origin === "llm_fallback") {
    return (
      <span className="inline-flex items-center gap-1 rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400">
        <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        Inferred by LLM
      </span>
    );
  }

  if (origin === "deterministic") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Cog className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        rule engine
      </span>
    );
  }

  // An origin the UI does not know about is shown verbatim rather than guessed at.
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <HelpCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {origin}
    </span>
  );
}

// Catalog tiers that carry no platform information: naming them adds nothing a
// reader can act on, and "low - not a diagnosis" already says it.
const CONTENTLESS_PATTERN_IDS = new Set([
  "generic.unknown",
  "generic.coded",
  "exception.generic",
  "exception.recoverable",
]);

export function isInformativePatternId(patternId?: string | null): boolean {
  return Boolean(patternId) && !CONTENTLESS_PATTERN_IDS.has(patternId as string);
}

/**
 * The catalog rule that matched, verbatim. Kept as the raw id rather than a
 * prettified label so it stays greppable against the catalog and quotable in a
 * bug report.
 */
export function PatternIdTag({ patternId }: { patternId?: string | null }) {
  if (!isInformativePatternId(patternId)) return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <span className="uppercase tracking-wide">rule</span>
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px] text-foreground">
        {patternId}
      </code>
    </span>
  );
}

// --- formatters -------------------------------------------------------------

export function formatDate(value: string | null | undefined): string {
  return value
    ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
        new Date(value),
      )
    : "-";
}

export function formatDayLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

export function formatDuration(value: number | null | undefined): string {
  if (value == null) return "-";
  if (value < 1000) return `${Math.round(value)}ms`;
  const seconds = value / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  const hours = minutes / 60;
  if (hours < 48) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

/** Compact figures for stat tiles and dense numeric columns (1,284 / 12.9K / 4.2M). */
export function formatCompact(value: number | null | undefined): string {
  if (value == null) return "-";
  if (Math.abs(value) < 10_000) return value.toLocaleString();
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function formatPercent(ratio: number | null | undefined): string {
  if (ratio == null) return "-";
  const pct = ratio * 100;
  if (pct > 0 && pct < 1) return "<1%";
  return `${Math.round(pct)}%`;
}

export function formatAge(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}

export function formatInterval(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}
