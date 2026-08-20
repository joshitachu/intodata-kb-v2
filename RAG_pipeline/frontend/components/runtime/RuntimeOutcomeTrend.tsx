"use client";

import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { RuntimeOutcomeTrendPoint, RuntimeTrendBucket } from "@/types";
import {
  Empty,
  EntityTable,
  formatCompact,
  formatDate,
  formatDayLabel,
  formatDuration,
  formatPercent,
} from "./runtime-ui";

/**
 * Outcome + duration trend.
 *
 * Form: two small multiples sharing one time axis rather than a dual-axis
 * chart — run counts and durations are different scales.
 *
 * Colour: the outcome chart uses the *emphasis* form — failures in the reserved
 * critical status hue against a de-emphasis gray for total volume. A
 * success-green vs failure-red pair was rejected: it measures CVD deltaE 4.1
 * under deuteranopia (validated against this app's surfaces), below the floor,
 * so it is not legal for adjacent marks. Emphasis removes the pair entirely.
 *
 * Validated with the dataviz palette validator against THIS app's chart
 * surfaces (light #ffffff, dark #020817), not the validator defaults:
 *
 *   emphasis pair #d03b3b + #64748b — both modes:
 *     CVD deltaE 12.4 (protan) · normal-vision 21.5 · contrast >= 3:1 · L in band.
 *     The gray fails the chroma floor by design: it is the de-emphasis channel,
 *     not a categorical identity slot.
 *
 *   duration ramp (ordinal, one hue, monotone lightness) — all checks PASS:
 *     light #86b6ef -> #2a78d6 · dark #6da7ec -> #3987e5
 */
const VIZ_CSS = `
.runtime-viz {
  --rv-fail: #d03b3b;
  --rv-rest: #64748b;
  --rv-p50: #86b6ef;
  --rv-p95: #2a78d6;
  --rv-grid: #e2e8f0;
  --rv-axis: #cbd5e1;
  --rv-surface: #ffffff;
}
.dark .runtime-viz {
  --rv-fail: #d03b3b;
  --rv-rest: #64748b;
  --rv-p50: #6da7ec;
  --rv-p95: #3987e5;
  --rv-grid: #1e293b;
  --rv-axis: #334155;
  --rv-surface: #020817;
}
`;

const BUCKET_NOUN: Record<RuntimeTrendBucket, string> = {
  hour: "hour",
  day: "day",
  week: "week",
};

interface Props {
  points: RuntimeOutcomeTrendPoint[];
  bucket: RuntimeTrendBucket;
}

export function RuntimeOutcomeTrend({ points, bucket }: Props) {
  const [view, setView] = useState<"chart" | "table">("chart");

  if (points.length === 0) {
    return <Empty label="No runs were recorded in this window." />;
  }

  const maxFailed = points.reduce((max, point) => Math.max(max, point.failed_runs), 0);
  const hasDuration = points.some(
    (point) => point.p50_duration_ms != null || point.p95_duration_ms != null,
  );

  return (
    <div className="runtime-viz flex flex-col gap-4">
      <style>{VIZ_CSS}</style>

      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Legend is always present for two or more series; identity never rests
            on colour alone. */}
        <ul className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
          <LegendKey color="var(--rv-rest)" label={`Runs per ${BUCKET_NOUN[bucket]}`} />
          <LegendKey color="var(--rv-fail)" label="Failed" />
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
        <TrendTable points={points} bucket={bucket} />
      ) : (
        <div className="flex flex-col gap-4">
          <figure
            className="m-0"
            aria-label={`Runs and failures per ${BUCKET_NOUN[bucket]}`}
          >
            <ResponsiveContainer width="100%" height={172}>
              <BarChart data={points} barGap={2} margin={{ top: 16, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--rv-grid)" strokeWidth={1} vertical={false} />
                <XAxis
                  dataKey="bucket_start"
                  tickFormatter={formatDayLabel}
                  tick={{ fontSize: 11, fill: "currentColor" }}
                  className="text-muted-foreground"
                  stroke="var(--rv-axis)"
                  tickLine={false}
                  interval="preserveStartEnd"
                  minTickGap={24}
                />
                <YAxis
                  allowDecimals={false}
                  tickFormatter={formatCompact}
                  tick={{ fontSize: 11, fill: "currentColor" }}
                  className="text-muted-foreground"
                  stroke="var(--rv-axis)"
                  tickLine={false}
                  width={44}
                />
                <Tooltip content={<TrendTooltip />} cursor={{ fill: "var(--rv-grid)" }} />
                <Bar
                  dataKey="total_runs"
                  name="Runs"
                  fill="var(--rv-rest)"
                  maxBarSize={24}
                  radius={[4, 4, 0, 0]}
                  isAnimationActive={false}
                />
                <Bar
                  dataKey="failed_runs"
                  name="Failed"
                  fill="var(--rv-fail)"
                  maxBarSize={24}
                  radius={[4, 4, 0, 0]}
                  isAnimationActive={false}
                >
                  {/* Label the extreme only — a number on every column goes unread. */}
                  <LabelList dataKey="failed_runs" content={<PeakLabel peak={maxFailed} />} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </figure>

          {hasDuration && (
            <figure className="m-0" aria-label="Run duration percentiles">
              <ResponsiveContainer width="100%" height={132}>
                <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--rv-grid)" strokeWidth={1} vertical={false} />
                  <XAxis
                    dataKey="bucket_start"
                    tickFormatter={formatDayLabel}
                    tick={{ fontSize: 11, fill: "currentColor" }}
                    className="text-muted-foreground"
                    stroke="var(--rv-axis)"
                    tickLine={false}
                    interval="preserveStartEnd"
                    minTickGap={24}
                  />
                  <YAxis
                    tickFormatter={(value: number) => formatDuration(value)}
                    tick={{ fontSize: 11, fill: "currentColor" }}
                    className="text-muted-foreground"
                    stroke="var(--rv-axis)"
                    tickLine={false}
                    width={56}
                  />
                  <Tooltip content={<TrendTooltip />} cursor={{ stroke: "var(--rv-axis)" }} />
                  <Line
                    type="monotone"
                    dataKey="p50_duration_ms"
                    name="p50 duration"
                    stroke="var(--rv-p50)"
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    dot={{ r: 4, fill: "var(--rv-p50)", stroke: "var(--rv-surface)", strokeWidth: 2 }}
                    connectNulls
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="p95_duration_ms"
                    name="p95 duration"
                    stroke="var(--rv-p95)"
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    dot={{ r: 4, fill: "var(--rv-p95)", stroke: "var(--rv-surface)", strokeWidth: 2 }}
                    connectNulls
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </figure>
          )}
        </div>
      )}
    </div>
  );
}

function LegendKey({ color, label, line }: { color: string; label: string; line?: boolean }) {
  return (
    <li className="flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className={line ? "h-0.5 w-3 rounded-full" : "h-2.5 w-2.5 rounded-sm"}
        style={{ backgroundColor: color }}
      />
      {label}
    </li>
  );
}

/** Renders the value only on the worst bucket, so the label stays meaningful. */
function PeakLabel(props: {
  peak?: number;
  value?: number | string;
  x?: number | string;
  y?: number | string;
  width?: number | string;
}) {
  const { peak = 0, value, x, y, width } = props;
  const numeric = typeof value === "number" ? value : Number(value ?? 0);
  if (!peak || numeric !== peak) return null;
  const left = Number(x ?? 0) + Number(width ?? 0) / 2;
  return (
    <text
      x={left}
      y={Number(y ?? 0) - 6}
      textAnchor="middle"
      className="fill-foreground text-[10px] font-medium"
    >
      {numeric.toLocaleString()}
    </text>
  );
}

function TrendTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: RuntimeOutcomeTrendPoint }>;
}) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs shadow-[var(--elevation-md)]">
      <p className="font-medium text-foreground">{formatDate(point.bucket_start)}</p>
      <dl className="mt-1 grid grid-cols-[auto_auto] gap-x-3 text-muted-foreground">
        <dt>Runs</dt>
        <dd className="text-right tabular-nums text-foreground">
          {point.total_runs.toLocaleString()}
        </dd>
        <dt>Failed</dt>
        <dd className="text-right tabular-nums text-foreground">
          {point.failed_runs.toLocaleString()} ({formatPercent(point.failure_rate)})
        </dd>
        {point.p50_duration_ms != null && (
          <>
            <dt>p50</dt>
            <dd className="text-right tabular-nums text-foreground">
              {formatDuration(point.p50_duration_ms)}
            </dd>
          </>
        )}
        {point.p95_duration_ms != null && (
          <>
            <dt>p95</dt>
            <dd className="text-right tabular-nums text-foreground">
              {formatDuration(point.p95_duration_ms)}
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

/** The text alternative to the chart — also the relief channel for contrast. */
function TrendTable({
  points,
  bucket,
}: {
  points: RuntimeOutcomeTrendPoint[];
  bucket: RuntimeTrendBucket;
}) {
  return (
    <EntityTable
      headers={[BUCKET_NOUN[bucket], "Runs", "Failed", "Failure rate", "p50", "p95"]}
      rows={points}
      rowKey={(point) => point.bucket_start}
      numeric={[1, 2, 3, 4, 5]}
      cells={(point) => [
        formatDate(point.bucket_start),
        point.total_runs.toLocaleString(),
        point.failed_runs.toLocaleString(),
        formatPercent(point.failure_rate),
        formatDuration(point.p50_duration_ms),
        formatDuration(point.p95_duration_ms),
      ]}
    />
  );
}
