"use client";

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type {
  RuntimeOutcomeTrendPoint,
  RuntimeTrendBucket,
  RuntimeTrendCoverage,
} from "@/types";
import {
  Empty,
  EntityTable,
  formatCompact,
  formatDayLabel,
  formatDuration,
} from "./runtime-ui";

const VIZ_CSS = `
.runtime-viz {
  --rv-fail: #d03b3b;
  --rv-volume: #64748b;
  --rv-p50: #86b6ef;
  --rv-p95: #2a78d6;
  --rv-grid: #e2e8f0;
  --rv-axis: #cbd5e1;
  --rv-surface: #ffffff;
  --rv-gap: #e2e8f0;
  --rv-boundary: #64748b;
}
.dark .runtime-viz {
  --rv-fail: #e05a5a;
  --rv-volume: #8291a8;
  --rv-p50: #6da7ec;
  --rv-p95: #3987e5;
  --rv-grid: #1e293b;
  --rv-axis: #334155;
  --rv-surface: #020817;
  --rv-gap: #273449;
  --rv-boundary: #94a3b8;
}
`;

const BUCKET_NOUN: Record<RuntimeTrendBucket, string> = {
  hour: "hour",
  day: "day",
  week: "week",
};

export type TrendDisplayPoint = RuntimeOutcomeTrendPoint & {
  has_captured_records: boolean;
  is_partial: boolean;
  volume_plot: number | null;
  failure_rate_plot: number | null;
  p50_plot: number | null;
  p95_plot: number | null;
};

export interface DurationScale {
  mode: "linear" | "log";
  domain: [number, number];
  ticks?: number[];
  formatTick: (value: number) => string;
  plot: (value: number | null | undefined) => number | null;
  min: number;
  max: number;
}

interface Props {
  points: RuntimeOutcomeTrendPoint[];
  bucket: RuntimeTrendBucket;
  coverage: RuntimeTrendCoverage;
  rangeStart: string;
  rangeEnd: string;
}

export function RuntimeOutcomeTrend({
  points,
  bucket,
  coverage,
  rangeStart,
  rangeEnd,
}: Props) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const orderedPoints = useMemo(
    () => [...points].sort((left, right) => Date.parse(left.bucket_start) - Date.parse(right.bucket_start)),
    [points],
  );
  const chartPoints = useMemo(
    () => alignTrendPoints(orderedPoints, bucket, rangeStart, rangeEnd),
    [bucket, orderedPoints, rangeEnd, rangeStart],
  );
  const p50Scale = useMemo(
    () => buildDurationScale(chartPoints.map((point) => ({ ...point, p95_duration_ms: null }))),
    [chartPoints],
  );
  const p95Scale = useMemo(
    () => buildDurationScale(chartPoints.map((point) => ({ ...point, p50_duration_ms: null }))),
    [chartPoints],
  );
  const plottedPoints = useMemo<TrendDisplayPoint[]>(
    () => chartPoints.map((point) => ({
      ...point,
      p50_plot: point.has_captured_records ? p50Scale.plot(point.p50_duration_ms) : null,
      p95_plot: point.has_captured_records ? p95Scale.plot(point.p95_duration_ms) : null,
    })),
    [chartPoints, p50Scale, p95Scale],
  );
  const coverageBoundary = useMemo(() => {
    if (!coverage.observed_from) return null;
    const boundary = bucketStart(coverage.observed_from, bucket)?.toISOString() ?? null;
    return boundary && plottedPoints.some((point) => point.bucket_start === boundary)
      ? boundary
      : null;
  }, [bucket, coverage.observed_from, plottedPoints]);
  const partialBucket = plottedPoints.find((point) => point.is_partial)?.bucket_start ?? null;
  const capturedPoints = plottedPoints.filter((point) => point.has_captured_records);
  const hasP50 = capturedPoints.some((point) => point.p50_duration_ms != null);
  const hasP95 = capturedPoints.some((point) => point.p95_duration_ms != null);
  const hasDuration = hasP50 || hasP95;

  return (
    <div className="runtime-viz flex flex-col gap-4">
      <style>{VIZ_CSS}</style>
      <CoverageNotice coverage={coverage} />

      {orderedPoints.length === 0 ? (
        <Empty label="No captured records in this window." />
      ) : (
        <>
          <TrendSummary points={plottedPoints} bucket={bucket} />
          <CoverageRail points={plottedPoints} bucket={bucket} />

          <div className="flex flex-wrap items-center justify-between gap-3">
            <ul className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
              <LegendKey color="var(--rv-volume)" label="Captured runs" />
              <LegendKey color="var(--rv-fail)" label="Failure rate" line />
              {hasDuration && view === "chart" && (
                <>
                  <LegendKey color="var(--rv-p50)" label="p50 duration" line />
                  <LegendKey color="var(--rv-p95)" label="p95 duration" line />
                </>
              )}
            </ul>
            <div className="flex gap-1" role="group" aria-label="Trend view">
              {(["chart", "table"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setView(option)}
                  aria-pressed={view === option}
                  className={`rounded-md border px-2 py-1 text-xs capitalize ${
                    view === option
                      ? "border-primary text-primary"
                      : "border-input text-muted-foreground"
                  }`}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          {view === "table" ? (
            <TrendTable points={plottedPoints} bucket={bucket} />
          ) : (
            <div className="flex flex-col gap-4">
              <figure className="m-0" aria-label={`Captured runs per ${BUCKET_NOUN[bucket]}`}>
                <figcaption className="mb-1 text-[11px] font-medium text-muted-foreground">
                  Captured run volume
                </figcaption>
                <ResponsiveContainer width="100%" height={158}>
                  <BarChart data={plottedPoints} margin={{ top: 14, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke="var(--rv-grid)" strokeWidth={1} vertical={false} />
                    <TimeAxis bucket={bucket} />
                    <YAxis
                      allowDecimals={false}
                      tickFormatter={formatCompact}
                      tick={{ fontSize: 11, fill: "currentColor" }}
                      className="text-muted-foreground"
                      stroke="var(--rv-axis)"
                      tickLine={false}
                      width={48}
                    />
                    <Tooltip content={<TrendTooltip bucket={bucket} />} cursor={{ fill: "var(--rv-grid)" }} />
                    {coverageBoundary && (
                      <ReferenceLine
                        x={coverageBoundary}
                        stroke="var(--rv-boundary)"
                        strokeDasharray="3 3"
                        label={{ value: "Captured history starts", position: "insideTopLeft", fontSize: 10 }}
                      />
                    )}
                    {partialBucket && <PartialReferenceLine value={partialBucket} />}
                    <Bar
                      dataKey="volume_plot"
                      name="Captured runs"
                      fill="var(--rv-volume)"
                      maxBarSize={28}
                      radius={[4, 4, 0, 0]}
                      isAnimationActive={false}
                    >
                      {plottedPoints.map((point) => (
                        <Cell
                          key={point.bucket_start}
                          fill="var(--rv-volume)"
                          fillOpacity={point.is_partial ? 0.5 : 1}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </figure>

              <FailureRateChart
                points={plottedPoints}
                bucket={bucket}
                partialBucket={partialBucket}
              />

              {hasDuration ? (
                <section aria-label="Run duration percentiles">
                  <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
                    <span className="font-medium">Duration by percentile · independent scales</span>
                    <span>Compare each percentile with itself over time</span>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    {hasP50 && (
                      <DurationSeriesChart
                        points={plottedPoints}
                        bucket={bucket}
                        dataKey="p50_plot"
                        label="Typical duration (p50)"
                        color="var(--rv-p50)"
                        scale={p50Scale}
                        partialBucket={partialBucket}
                      />
                    )}
                    {hasP95 && (
                      <DurationSeriesChart
                        points={plottedPoints}
                        bucket={bucket}
                        dataKey="p95_plot"
                        label="Slow-tail duration (p95)"
                        color="var(--rv-p95)"
                        scale={p95Scale}
                        partialBucket={partialBucket}
                      />
                    )}
                  </div>
                </section>
              ) : (
                <div className="rounded-lg border border-dashed border-border px-4 py-5 text-center text-xs text-muted-foreground">
                  No completed-run duration data in captured records for this window.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function DurationSeriesChart({
  points,
  bucket,
  dataKey,
  label,
  color,
  scale,
  partialBucket,
}: {
  points: TrendDisplayPoint[];
  bucket: RuntimeTrendBucket;
  dataKey: "p50_plot" | "p95_plot";
  label: string;
  color: string;
  scale: DurationScale;
  partialBucket: string | null;
}) {
  return (
    <figure className="m-0 rounded-lg border border-border/60 bg-muted/10 px-2 pt-2" aria-label={label}>
      <figcaption className="flex items-center justify-between gap-2 px-1 text-[10px] text-muted-foreground">
        <span className="font-medium text-foreground">{label}</span>
        <span>{scale.mode === "log" ? "Log · within-series outlier" : "Linear"}</span>
      </figcaption>
      <ResponsiveContainer width="100%" height={132}>
        <LineChart data={points} margin={{ top: 8, right: 6, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--rv-grid)" strokeWidth={1} vertical={false} />
          <TimeAxis bucket={bucket} />
          <YAxis
            domain={scale.domain}
            ticks={scale.ticks}
            tickFormatter={scale.formatTick}
            tick={{ fontSize: 10, fill: "currentColor" }}
            className="text-muted-foreground"
            stroke="var(--rv-axis)"
            tickLine={false}
            width={58}
          />
          <Tooltip content={<TrendTooltip bucket={bucket} />} cursor={{ stroke: "var(--rv-axis)" }} />
          {partialBucket && <PartialReferenceLine value={partialBucket} />}
          <Line
            type="monotone"
            dataKey={dataKey}
            name={label}
            stroke={color}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            dot={{ r: 3.25, fill: color, stroke: "var(--rv-surface)", strokeWidth: 2 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}

function TimeAxis({ bucket }: { bucket: RuntimeTrendBucket }) {
  return (
    <XAxis
      dataKey="bucket_start"
      tickFormatter={(value: string) => formatBucketAxisLabel(value, bucket)}
      tick={{ fontSize: 11, fill: "currentColor" }}
      className="text-muted-foreground"
      stroke="var(--rv-axis)"
      tickLine={false}
      interval="preserveStartEnd"
      minTickGap={24}
    />
  );
}

function PartialReferenceLine({ value }: { value: string }) {
  return (
    <ReferenceLine
      x={value}
      stroke="var(--rv-axis)"
      strokeDasharray="2 3"
      label={{ value: "Partial", position: "insideTopRight", fontSize: 10 }}
    />
  );
}

function FailureRateChart({
  points,
  bucket,
  partialBucket,
}: {
  points: TrendDisplayPoint[];
  bucket: RuntimeTrendBucket;
  partialBucket: string | null;
}) {
  const captured = points.filter((point) => point.has_captured_records);
  const peak = captured.reduce<TrendDisplayPoint | null>(
    (current, point) => !current || point.failure_rate > current.failure_rate ? point : current,
    null,
  );
  const maximum = Math.max(0, ...captured.map((point) => point.failure_rate));
  const domainMaximum = maximum === 0 ? 0.01 : Math.max(0.01, maximum * 1.18);

  return (
    <figure className="m-0" aria-label={`Failure rate per captured ${BUCKET_NOUN[bucket]}`}>
      <figcaption className="mb-1 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span className="font-medium">Failure rate per captured bucket</span>
        {peak && (
          <span>
            Peak {formatRate(peak.failure_rate)} · {peak.failed_runs.toLocaleString()} of {peak.total_runs.toLocaleString()} · {bucketDateLabel(peak.bucket_start, bucket)}
          </span>
        )}
      </figcaption>
      <ResponsiveContainer width="100%" height={116}>
        <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--rv-grid)" strokeWidth={1} vertical={false} />
          <TimeAxis bucket={bucket} />
          <YAxis
            domain={[0, domainMaximum]}
            tickFormatter={formatRateAxis}
            tick={{ fontSize: 11, fill: "currentColor" }}
            className="text-muted-foreground"
            stroke="var(--rv-axis)"
            tickLine={false}
            width={52}
          />
          <Tooltip content={<TrendTooltip bucket={bucket} />} cursor={{ stroke: "var(--rv-axis)" }} />
          {partialBucket && <PartialReferenceLine value={partialBucket} />}
          <Line
            type="monotone"
            dataKey="failure_rate_plot"
            name="Failure rate"
            stroke="var(--rv-fail)"
            strokeWidth={2.25}
            dot={{ r: 3.5, fill: "var(--rv-fail)", stroke: "var(--rv-surface)", strokeWidth: 2 }}
            activeDot={{ r: 5 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}

function CoverageNotice({ coverage }: { coverage: RuntimeTrendCoverage }) {
  if (!coverage.observed_from) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        No captured history is available for this scope. Capture completeness is unknown.
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-border/70 bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
      <span className="font-medium text-foreground">
        Captured history starts {formatCoverageDate(coverage.observed_from)}.
      </span>{" "}
      Gaps mean <span className="font-medium text-foreground">No captured records</span>, not confirmed zero runs.
    </div>
  );
}

function CoverageRail({
  points,
  bucket,
}: {
  points: TrendDisplayPoint[];
  bucket: RuntimeTrendBucket;
}) {
  const missing = points.filter((point) => !point.has_captured_records).length;
  const captured = points.length - missing;
  return (
    <div className="space-y-1.5" aria-label={`Capture coverage: ${captured} captured buckets, ${missing} with no captured records`}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <span className="font-medium uppercase tracking-wide">Capture coverage</span>
        <span>{captured} captured · {missing} No captured records</span>
      </div>
      <div
        className="grid h-2.5 overflow-hidden rounded-full border border-border"
        style={{ gridTemplateColumns: `repeat(${points.length}, minmax(2px, 1fr))` }}
      >
        {points.map((point) => {
          const state = point.has_captured_records ? "Captured records" : "No captured records";
          const partial = point.is_partial ? " · Partial bucket" : "";
          return (
            <span
              key={point.bucket_start}
              title={`${bucketDateLabel(point.bucket_start, bucket)} · ${state}${partial}`}
              className="border-r border-background/70 last:border-r-0"
              style={point.has_captured_records
                ? { background: "var(--rv-volume)", opacity: point.is_partial ? 0.5 : 1 }
                : { background: "repeating-linear-gradient(135deg, transparent 0 3px, var(--rv-gap) 3px 6px)" }}
            />
          );
        })}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
        <LegendKey color="var(--rv-volume)" label="Captured records" />
        <LegendKey color="var(--rv-gap)" label="No captured records" striped />
        {points.some((point) => point.is_partial) && <li>Partial bucket is shown at 50% opacity</li>}
      </ul>
    </div>
  );
}

/**
 * Use a logarithmic scale only for a genuine temporal outlier within p50 or
 * within p95. Comparing p50 directly with p95 would incorrectly classify a
 * healthy percentile separation as an outlier.
 */
export function buildDurationScale(points: RuntimeOutcomeTrendPoint[]): DurationScale {
  const p50 = durationValues(points, "p50_duration_ms");
  const p95 = durationValues(points, "p95_duration_ms");
  const values = [...p50, ...p95];
  if (values.length === 0) {
    return {
      mode: "linear",
      domain: [0, 1],
      formatTick: formatDuration,
      plot: (value) => value ?? null,
      min: 0,
      max: 0,
    };
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const useLog = seriesHasExtremeOutlier(p50) || seriesHasExtremeOutlier(p95);
  if (!useLog) {
    return {
      mode: "linear",
      domain: [0, Math.max(1, max * 1.08)],
      formatTick: formatDuration,
      plot: (value) => value ?? null,
      min,
      max,
    };
  }

  const plot = (value: number | null | undefined) => value == null ? null : Math.log10(value + 1);
  const lower = plot(min) ?? 0;
  const upper = plot(max) ?? 1;
  const span = Math.max(0.001, upper - lower);
  const ticks = Array.from({ length: 4 }, (_, index) => lower + (span * index) / 3);
  return {
    mode: "log",
    domain: [lower, upper],
    ticks,
    formatTick: (value) => formatDuration(Math.max(0, 10 ** value - 1)),
    plot,
    min,
    max,
  };
}

function durationValues(
  points: RuntimeOutcomeTrendPoint[],
  key: "p50_duration_ms" | "p95_duration_ms",
): number[] {
  return points
    .map((point) => point[key])
    .filter((value): value is number => value != null && Number.isFinite(value) && value >= 0);
}

function seriesHasExtremeOutlier(values: number[]): boolean {
  const positive = values.filter((value) => value > 0).sort((left, right) => left - right);
  if (positive.length < 2) return false;
  const largest = positive[positive.length - 1];
  const runnerUp = positive[positive.length - 2];
  const smallest = positive[0];
  return largest / runnerUp >= 25 && largest / smallest >= 100;
}

function bucketStart(value: string, bucket: RuntimeTrendBucket): Date | null {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  date.setUTCMinutes(0, 0, 0);
  if (bucket !== "hour") date.setUTCHours(0, 0, 0, 0);
  if (bucket === "week") {
    const daysSinceMonday = (date.getUTCDay() + 6) % 7;
    date.setUTCDate(date.getUTCDate() - daysSinceMonday);
  }
  return date;
}

function nextBucket(date: Date, bucket: RuntimeTrendBucket): Date {
  const next = new Date(date);
  if (bucket === "hour") next.setUTCHours(next.getUTCHours() + 1);
  if (bucket === "day") next.setUTCDate(next.getUTCDate() + 1);
  if (bucket === "week") next.setUTCDate(next.getUTCDate() + 7);
  return next;
}

function emptyPoint(bucket_start: string, isPartial: boolean): TrendDisplayPoint {
  return {
    bucket_start,
    total_runs: 0,
    success_runs: 0,
    failed_runs: 0,
    running_runs: 0,
    unknown_runs: 0,
    failure_rate: 0,
    p50_duration_ms: null,
    p95_duration_ms: null,
    max_duration_ms: null,
    has_captured_records: false,
    is_partial: isPartial,
    volume_plot: null,
    failure_rate_plot: null,
    p50_plot: null,
    p95_plot: null,
  };
}

/** Align the time axis without turning missing aggregates into zero-run facts. */
export function alignTrendPoints(
  points: RuntimeOutcomeTrendPoint[],
  bucket: RuntimeTrendBucket,
  rangeStart: string,
  rangeEnd: string,
): TrendDisplayPoint[] {
  if (points.length === 0) return [];
  const start = bucketStart(rangeStart, bucket);
  const end = bucketStart(rangeEnd, bucket);
  const endInstant = new Date(rangeEnd);
  if (!start || !end || Number.isNaN(endInstant.getTime()) || start > end) {
    return points.map((point) => ({
      ...point,
      has_captured_records: true,
      is_partial: false,
      volume_plot: point.total_runs,
      failure_rate_plot: point.failure_rate,
      p50_plot: point.p50_duration_ms ?? null,
      p95_plot: point.p95_duration_ms ?? null,
    }));
  }

  const byBucket = new Map<string, RuntimeOutcomeTrendPoint>();
  for (const point of points) {
    const normalized = bucketStart(point.bucket_start, bucket);
    if (normalized) byBucket.set(normalized.toISOString(), point);
  }

  const aligned: TrendDisplayPoint[] = [];
  for (let cursor = start; cursor <= end && aligned.length < 500; cursor = nextBucket(cursor, bucket)) {
    const key = cursor.toISOString();
    const point = byBucket.get(key);
    const isPartial = nextBucket(cursor, bucket).getTime() > endInstant.getTime();
    aligned.push(point ? {
      ...point,
      has_captured_records: true,
      is_partial: isPartial,
      volume_plot: point.total_runs,
      failure_rate_plot: point.failure_rate,
      p50_plot: point.p50_duration_ms ?? null,
      p95_plot: point.p95_duration_ms ?? null,
    } : emptyPoint(key, isPartial));
  }
  return aligned;
}

function LegendKey({
  color,
  label,
  line,
  striped,
}: {
  color: string;
  label: string;
  line?: boolean;
  striped?: boolean;
}) {
  return (
    <li className="flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className={line ? "h-0.5 w-3 rounded-full" : "h-2.5 w-2.5 rounded-sm"}
        style={striped
          ? { background: `repeating-linear-gradient(135deg, transparent 0 2px, ${color} 2px 4px)` }
          : { backgroundColor: color }}
      />
      {label}
    </li>
  );
}

function formatBucketAxisLabel(value: string, bucket: RuntimeTrendBucket): string {
  if (bucket !== "hour") return formatDayLabel(value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "UTC",
  }).format(date);
}

function bucketDateLabel(value: string, bucket: RuntimeTrendBucket): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  if (bucket === "hour") return `${formatBucketAxisLabel(value, bucket)} UTC`;
  const label = formatDayLabel(value);
  return bucket === "week" ? `Week of ${label}` : label;
}

function formatCoverageDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

function formatRate(ratio: number): string {
  const percentage = ratio * 100;
  if (percentage === 0) return "0%";
  if (percentage < 0.01) return "<0.01%";
  const digits = percentage < 10 ? 2 : 1;
  return `${percentage.toFixed(digits).replace(/\.0+$|(?<=\.[0-9])0$/, "")}%`;
}

function formatRateAxis(ratio: number): string {
  const percentage = ratio * 100;
  return `${percentage < 1 ? percentage.toFixed(1) : percentage.toFixed(0)}%`;
}

function TrendSummary({
  points,
  bucket,
}: {
  points: TrendDisplayPoint[];
  bucket: RuntimeTrendBucket;
}) {
  const captured = points.filter((point) => point.has_captured_records);
  const missing = points.length - captured.length;
  const busiest = captured.reduce<TrendDisplayPoint | null>(
    (current, point) => !current || point.total_runs > current.total_runs ? point : current,
    null,
  );
  const peakFailure = captured.reduce<TrendDisplayPoint | null>(
    (current, point) => !current || point.failure_rate > current.failure_rate ? point : current,
    null,
  );
  const slowest = captured.reduce<TrendDisplayPoint | null>(
    (current, point) => point.p95_duration_ms != null && (
      !current || (current.p95_duration_ms ?? -1) < point.p95_duration_ms
    ) ? point : current,
    null,
  );

  const items = [
    {
      label: "Captured activity",
      value: `${captured.length} ${captured.length === 1 ? "bucket" : "buckets"}`,
      hint: `${missing} No captured records`,
    },
    {
      label: "Busiest",
      value: busiest ? `${formatCompact(busiest.total_runs)} runs` : "—",
      hint: busiest ? `${bucketDateLabel(busiest.bucket_start, bucket)}${busiest.is_partial ? " · partial" : ""}` : "No captured records",
    },
    {
      label: "Peak failure rate",
      value: peakFailure ? formatRate(peakFailure.failure_rate) : "—",
      hint: peakFailure
        ? `${peakFailure.failed_runs.toLocaleString()} of ${peakFailure.total_runs.toLocaleString()} · ${bucketDateLabel(peakFailure.bucket_start, bucket)}${peakFailure.is_partial ? " · partial" : ""}`
        : "No captured records",
    },
    {
      label: "Slowest p95",
      value: slowest ? formatDuration(slowest.p95_duration_ms) : "—",
      hint: slowest ? `${bucketDateLabel(slowest.bucket_start, bucket)}${slowest.is_partial ? " · partial" : ""}` : "No duration data",
    },
  ];

  return (
    <dl className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="min-w-0 rounded-lg border border-border/70 bg-muted/20 px-3 py-2.5">
          <dt className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {item.label}
          </dt>
          <dd className="mt-0.5 truncate text-sm font-semibold tabular-nums text-foreground">
            {item.value}
          </dd>
          <dd className="truncate text-[10px] text-muted-foreground" title={item.hint}>
            {item.hint}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function TrendTooltip({
  active,
  payload,
  bucket,
}: {
  active?: boolean;
  payload?: Array<{ payload?: TrendDisplayPoint }>;
  bucket: RuntimeTrendBucket;
}) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs shadow-[var(--elevation-md)]">
      <p className="font-medium text-foreground">
        {bucketDateLabel(point.bucket_start, bucket)}{point.is_partial ? " · Partial bucket" : ""}
      </p>
      {!point.has_captured_records ? (
        <p className="mt-1 text-muted-foreground">No captured records. Zero activity is not confirmed.</p>
      ) : (
        <dl className="mt-1 grid grid-cols-[auto_auto] gap-x-3 text-muted-foreground">
          <dt>Captured runs</dt>
          <dd className="text-right tabular-nums text-foreground">{point.total_runs.toLocaleString()}</dd>
          <dt>Failed</dt>
          <dd className="text-right tabular-nums text-foreground">
            {point.failed_runs.toLocaleString()} ({formatRate(point.failure_rate)})
          </dd>
          {point.p50_duration_ms != null && (
            <>
              <dt>p50</dt>
              <dd className="text-right tabular-nums text-foreground">{formatDuration(point.p50_duration_ms)}</dd>
            </>
          )}
          {point.p95_duration_ms != null && (
            <>
              <dt>p95</dt>
              <dd className="text-right tabular-nums text-foreground">{formatDuration(point.p95_duration_ms)}</dd>
            </>
          )}
        </dl>
      )}
    </div>
  );
}

function TrendTable({
  points,
  bucket,
}: {
  points: TrendDisplayPoint[];
  bucket: RuntimeTrendBucket;
}) {
  return (
    <EntityTable
      headers={[BUCKET_NOUN[bucket], "Capture", "Runs", "Failed", "Failure rate", "p50", "p95"]}
      rows={points}
      rowKey={(point) => point.bucket_start}
      numeric={[2, 3, 4, 5, 6]}
      cells={(point) => point.has_captured_records ? [
        `${bucketDateLabel(point.bucket_start, bucket)}${point.is_partial ? " · Partial" : ""}`,
        "Captured records",
        point.total_runs.toLocaleString(),
        point.failed_runs.toLocaleString(),
        formatRate(point.failure_rate),
        formatDuration(point.p50_duration_ms),
        formatDuration(point.p95_duration_ms),
      ] : [
        `${bucketDateLabel(point.bucket_start, bucket)}${point.is_partial ? " · Partial" : ""}`,
        "No captured records",
        "—",
        "—",
        "—",
        "—",
        "—",
      ]}
    />
  );
}
