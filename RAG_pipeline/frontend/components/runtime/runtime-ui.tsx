"use client";

import { useEffect, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Cog,
  HelpCircle,
  Loader2,
  RefreshCw,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Shared chrome + formatters for the runtime console. Extracted from
// RuntimeConsole so the aggregate panels render identical panels, tables and
// pagination instead of each re-inventing them. Everything here is built on the
// app's design tokens (rounded-xl surfaces, elevation shadows, muted borders) so
// the console reads as part of Nexus rather than a raw wireframe.

export const PAGE_SIZE = 50;

export function Panel({
  title,
  description,
  action,
  icon: Icon,
  children,
  /** Removes the body padding — for panels whose child is a full-bleed table. */
  flush = false,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: LucideIcon;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card shadow-elevation-sm">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/60 px-5 py-4">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            {Icon && <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />}
            {title}
          </h2>
          {description && (
            <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
      </div>
      <div className={flush ? "" : "p-5"}>{children}</div>
    </section>
  );
}

export type KpiTone = "neutral" | "good" | "warning" | "danger";

const KPI_TILE_TONES: Record<KpiTone, string> = {
  neutral: "bg-primary/10 text-primary",
  good: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400",
  warning: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400",
  danger: "bg-destructive/10 text-destructive",
};

const KPI_VALUE_TONES: Record<KpiTone, string> = {
  neutral: "text-foreground",
  good: "text-foreground",
  warning: "text-amber-700 dark:text-amber-400",
  danger: "text-destructive",
};

/**
 * Stat tile: label, value, optional hint. The tone is carried by BOTH the icon
 * tile and the value, so state is never encoded in colour alone — the icon is
 * the redundant channel.
 */
export function Kpi({
  icon: Icon,
  label,
  value,
  hint,
  tone = "neutral",
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint?: string;
  tone?: KpiTone;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-elevation-sm">
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <span
          className={cn(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
            KPI_TILE_TONES[tone],
          )}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden />
        </span>
      </div>
      <p className={cn("mt-2 text-2xl font-semibold tabular-nums", KPI_VALUE_TONES[tone])}>
        {value}
      </p>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function Empty({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      <span className="flex h-9 w-9 items-center justify-center rounded-full border border-border bg-muted/40 text-muted-foreground">
        <CircleSlash className="h-4 w-4" aria-hidden />
      </span>
      <p className="text-sm text-muted-foreground">{label}</p>
      {hint && <p className="max-w-sm text-xs text-muted-foreground/80">{hint}</p>}
    </div>
  );
}

type QueryLike = { isLoading: boolean; isError: boolean; refetch: () => unknown };

/** Shimmerless skeleton — a shape placeholder reads as loading without motion. */
function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-muted motion-reduce:animate-none", className)} />;
}

export function QueryState({
  queries,
  children,
}: {
  queries: QueryLike[];
  children: ReactNode;
}) {
  if (queries.some((query) => query.isLoading)) {
    return (
      <div className="flex flex-col gap-4" aria-busy="true" aria-live="polite">
        <span className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
          Loading runtime data
        </span>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} className="h-24 rounded-xl" />
          ))}
        </div>
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }
  if (queries.some((query) => query.isError)) {
    return (
      <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-5">
        <p className="flex items-center gap-2 text-sm font-medium text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
          Some runtime data could not be loaded.
        </p>
        <Button
          variant="secondary"
          size="sm"
          className="mt-3"
          onClick={() => queries.forEach((query) => query.refetch())}
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          Retry
        </Button>
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
  mono,
  emptyLabel = "No records are available for this scope.",
}: {
  headers: string[];
  rows: T[];
  rowKey: (item: T) => string;
  onClick?: (item: T) => void;
  cells: (item: T) => ReactNode[];
  /** Column indexes rendered right-aligned with tabular figures. */
  numeric?: number[];
  /** Column indexes rendered monospace — ids, hashes, codes. */
  mono?: number[];
  emptyLabel?: string;
}) {
  if (rows.length === 0) return <Empty label={emptyLabel} />;
  const numericSet = new Set(numeric ?? []);
  const monoSet = new Set(mono ?? []);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[620px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40">
            {headers.map((header, index) => (
              <th
                key={header}
                scope="col"
                className={cn(
                  "whitespace-nowrap px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-muted-foreground",
                  numericSet.has(index) && "text-right",
                )}
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
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
                "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none",
                onClick && "cursor-pointer hover:bg-muted/50",
              )}
            >
              {cells(item).map((cell, index) => (
                <td
                  key={`${rowKey(item)}:${headers[index]}`}
                  className={cn(
                    "max-w-64 truncate px-4 py-2.5 align-middle",
                    index === 0 ? "font-medium text-foreground" : "text-muted-foreground",
                    numericSet.has(index) && "text-right tabular-nums text-foreground",
                    monoSet.has(index) && "font-mono text-xs",
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
  const first = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const last = Math.min(total, page * PAGE_SIZE + rows.length);

  return (
    <>
      {render(rows)}
      {/* Owns its own padding so it sits correctly inside a `flush` panel,
          where the table is full-bleed. */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 px-4 py-2.5 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {total === 0
            ? "0 records"
            : `Showing ${first.toLocaleString()}-${last.toLocaleString()} of ${total.toLocaleString()} records`}
        </span>
        {pages > 1 && (
          <div className="flex items-center gap-2">
            <span className="tabular-nums">
              Page {page + 1} of {pages}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={page === 0}
              onClick={() => onPage(page - 1)}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={page + 1 >= pages}
              onClick={() => onPage(page + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </div>
    </>
  );
}

const SEVERITY_STYLES: Record<string, string> = {
  error: "border-destructive/30 bg-destructive/10 text-destructive",
  warning: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  info: "border-border bg-muted text-muted-foreground",
  debug: "border-border/60 bg-muted/60 text-muted-foreground",
};

export function SeverityChip({ severity }: { severity: string }) {
  return (
    <span
      className={cn(
        "inline-block w-14 shrink-0 rounded border px-1 py-0.5 text-center text-[9px] font-semibold uppercase tracking-wide",
        SEVERITY_STYLES[severity] ?? "border-border bg-muted text-muted-foreground",
      )}
    >
      {severity}
    </span>
  );
}

// --- live status ------------------------------------------------------------

/**
 * Re-renders on an interval so relative timestamps stay honest without the
 * caller re-fetching. One timer per mounted consumer; the console mounts few.
 */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/**
 * "3m ago", with the exact timestamp on hover.
 *
 * On an operations console the question is almost always "how long ago", not
 * "on what date" -- but the absolute value must stay one hover away, because it
 * is what someone pastes into a ticket.
 */
export function RelativeTime({ value }: { value: string | null | undefined }) {
  const now = useNow();
  if (!value) return <span className="text-muted-foreground">-</span>;
  const ms = new Date(value).getTime();
  if (Number.isNaN(ms)) return <span className="text-muted-foreground">-</span>;
  return (
    <time dateTime={value} title={formatDate(value)} className="tabular-nums">
      {formatAge(now - ms)} ago
    </time>
  );
}

/** Pulsing dot for a live feed; static when the feed is stale or paused. */
export function LiveDot({ live }: { live: boolean }) {
  return (
    <span className="relative flex h-2 w-2 shrink-0" aria-hidden>
      {live && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60 motion-reduce:animate-none" />
      )}
      <span
        className={cn(
          "relative inline-flex h-2 w-2 rounded-full",
          live ? "bg-emerald-500" : "bg-muted-foreground/40",
        )}
      />
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

/** Clock time only — for dense log lines where the date is already established. */
export function formatClock(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
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
