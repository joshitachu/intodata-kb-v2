import type { RuntimeTrendBucket } from "@/types";

// The console's time window. Every aggregate query is scoped by it, which is
// also what keeps decommissioned jobs out of "needs attention": a job that last
// ran outside the window simply has no rows in the window-scoped rollup.

export type RuntimeWindowKey = "24h" | "7d" | "30d" | "90d";

export interface RuntimeWindow {
  key: RuntimeWindowKey;
  label: string;
  hours: number;
  /** Bucket granularity that keeps the series legible at this width. */
  bucket: RuntimeTrendBucket;
}

export const RUNTIME_WINDOWS: RuntimeWindow[] = [
  { key: "24h", label: "Last 24 hours", hours: 24, bucket: "hour" },
  { key: "7d", label: "Last 7 days", hours: 24 * 7, bucket: "day" },
  { key: "30d", label: "Last 30 days", hours: 24 * 30, bucket: "day" },
  { key: "90d", label: "Last 90 days", hours: 24 * 90, bucket: "week" },
];

export const DEFAULT_WINDOW_KEY: RuntimeWindowKey = "30d";

export function resolveWindow(key: RuntimeWindowKey): RuntimeWindow {
  return RUNTIME_WINDOWS.find((item) => item.key === key) ?? RUNTIME_WINDOWS[2];
}

/**
 * Inclusive lower bound for the window, as an ISO instant.
 *
 * `now` is injectable so callers (and tests) control the clock rather than
 * depending on the wall time at render.
 */
export function windowSince(key: RuntimeWindowKey, now: number = Date.now()): string {
  return new Date(now - resolveWindow(key).hours * 3_600_000).toISOString();
}
