// Pure runtime-health domain logic for the topology overlay (S3).
//
// Two responsibilities, both deterministic and unit-tested (no React, no I/O):
//   1. deriveStatus() - turn a RuntimeJobHealth aggregate into a coarse
//      operational status per the rule in the architecture plan (§2.3).
//   2. joinHealthToJobs() - the honest identity join between scanner job
//      names and runtime task names. Scanner names and TMC task names do NOT
//      reliably match (verified on live data), so we try exact -> normalized
//      -> prefix, aggregate 1:many matches worst-status-wins, and leave
//      everything unmatched as `unknown`. Health is NEVER fabricated.

import type { RuntimeExecutionState, RuntimeJobHealth } from "@/types/runtime";

export type JobStatus = "down" | "degraded" | "running" | "healthy" | "unknown";

/** failure_rate at/above this (with a non-failed latest run) reads as degraded. */
export const DEGRADED_FAILURE_RATE_THRESHOLD = 0.3;

// Higher = more severe. Used for worst-status-wins aggregation when one
// scanner job maps to many runtime tasks.
const STATUS_SEVERITY: Record<JobStatus, number> = {
  down: 5,
  degraded: 4,
  running: 3,
  healthy: 2,
  unknown: 1,
};

export interface DeriveStatusInput {
  latestState: RuntimeExecutionState | null | undefined;
  failureRate: number;
  currentFailureStreak: number;
}

/**
 * Coarse status from a runtime health aggregate. Order matters and mirrors
 * the plan's evaluation order: down > degraded > running > healthy > unknown.
 */
// Latest-run states that mean a human still needs to look. Mirrors the
// backend's `_ATTENTION_STATES`: a misfire never ran at all and a timeout was
// killed mid-flight, so both are outages even though neither is `failed`.
// `terminated` is excluded there on the grounds that an operator already
// intervened -- but its last run still did not complete, so it must not reach
// the `healthy` branch either.
const DOWN_STATES: ReadonlySet<string> = new Set(["failed", "misfired", "timed_out"]);

export function deriveStatus(input: DeriveStatusInput): JobStatus {
  const { latestState, failureRate, currentFailureStreak } = input;
  if (latestState && DOWN_STATES.has(latestState)) return "down";
  // latestState is now guaranteed to be none of the DOWN_STATES.
  if (failureRate >= DEGRADED_FAILURE_RATE_THRESHOLD || currentFailureStreak > 0) {
    return "degraded";
  }
  if (latestState === "running") return "running";
  if (latestState === "terminated") return "degraded";
  if (latestState === "success") return "healthy";
  return "unknown";
}

export function deriveStatusFromHealth(row: RuntimeJobHealth): JobStatus {
  return deriveStatus({
    latestState: row.latest_state ?? null,
    failureRate: row.failure_rate,
    currentFailureStreak: row.current_failure_streak,
  });
}

/** Most severe of a set of statuses (worst-status-wins). Empty -> unknown. */
export function worstStatus(statuses: readonly JobStatus[]): JobStatus {
  let worst: JobStatus = "unknown";
  for (const s of statuses) {
    if (STATUS_SEVERITY[s] > STATUS_SEVERITY[worst]) worst = s;
  }
  return worst;
}

/**
 * Normalize a job/task name for fuzzy matching: trim, lowercase, and strip a
 * trailing version suffix (`_0.1`, `_v2`, `_1`). TMC and Studio names differ
 * in case and version tags; this collapses those differences without
 * inventing matches.
 */
export function normalizeJobName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/_v?\d+(?:\.\d+)*$/, "");
}

export interface JobHealthMatch {
  status: JobStatus;
  /** The runtime rows that mapped to this scan job (1:many for TMC deploys). */
  matched: RuntimeJobHealth[];
  /** How the match was made, for transparency/debugging. */
  matchKind: "exact" | "normalized" | "prefix" | "none";
  /** Freshest latest_run_at across matched rows (ISO string), or null. */
  latestRunAt: string | null;
}

export interface JobHealthJoin {
  /** Every scan job -> its match (unmatched jobs carry status "unknown"). */
  byJob: Map<string, JobHealthMatch>;
  /** Convenience: scan job -> status only (unmatched -> "unknown"). */
  statusByJob: Map<string, JobStatus>;
  /** Count of scan jobs that matched at least one runtime row. */
  matchCount: number;
  /** Freshest latest_run_at across ALL matched rows (ISO string), or null. */
  latestRunAt: string | null;
}

function maxIso(a: string | null, b: string | null | undefined): string | null {
  if (!b) return a;
  if (!a) return b;
  return b > a ? b : a;
}

/**
 * Join scanner job names to runtime health rows. For each scan job we try, in
 * order: exact name equality, normalized equality, then normalized prefix
 * (`scan_` is a prefix of the runtime task, the endpoint-suffixed 1:many
 * case). The first tier that yields rows wins; its rows are aggregated
 * worst-status-wins. No match -> `unknown`.
 */
export function joinHealthToJobs(
  scanJobNames: readonly string[],
  healthRows: readonly RuntimeJobHealth[],
): JobHealthJoin {
  // Index runtime rows for exact and normalized lookups.
  const byExact = new Map<string, RuntimeJobHealth[]>();
  const byNormalized = new Map<string, RuntimeJobHealth[]>();
  const normalizedRows: { norm: string; row: RuntimeJobHealth }[] = [];
  for (const row of healthRows) {
    const exact = row.job_name;
    (byExact.get(exact) ?? byExact.set(exact, []).get(exact)!).push(row);
    const norm = normalizeJobName(row.job_name);
    (byNormalized.get(norm) ?? byNormalized.set(norm, []).get(norm)!).push(row);
    normalizedRows.push({ norm, row });
  }

  const byJob = new Map<string, JobHealthMatch>();
  const statusByJob = new Map<string, JobStatus>();
  let matchCount = 0;
  let latestRunAt: string | null = null;

  for (const scanJob of scanJobNames) {
    const scanNorm = normalizeJobName(scanJob);

    let matched: RuntimeJobHealth[] = [];
    let matchKind: JobHealthMatch["matchKind"] = "none";

    const exactRows = byExact.get(scanJob);
    if (exactRows && exactRows.length > 0) {
      matched = exactRows;
      matchKind = "exact";
    } else {
      const normRows = byNormalized.get(scanNorm);
      if (normRows && normRows.length > 0) {
        matched = normRows;
        matchKind = "normalized";
      } else if (scanNorm.length > 0) {
        // Prefix (1:many): one Studio job deployed as several endpoint-suffixed
        // TMC tasks, e.g. rItem_OnRamp -> rItem_OnRamp_BusinessCentral.
        const prefix = `${scanNorm}_`;
        const prefixRows = normalizedRows
          .filter((entry) => entry.norm.startsWith(prefix))
          .map((entry) => entry.row);
        if (prefixRows.length > 0) {
          matched = prefixRows;
          matchKind = "prefix";
        }
      }
    }

    if (matched.length === 0) {
      byJob.set(scanJob, { status: "unknown", matched: [], matchKind: "none", latestRunAt: null });
      statusByJob.set(scanJob, "unknown");
      continue;
    }

    const status = worstStatus(matched.map(deriveStatusFromHealth));
    let jobLatest: string | null = null;
    for (const row of matched) jobLatest = maxIso(jobLatest, row.latest_run_at ?? null);
    latestRunAt = maxIso(latestRunAt, jobLatest);

    byJob.set(scanJob, { status, matched, matchKind, latestRunAt: jobLatest });
    statusByJob.set(scanJob, status);
    matchCount += 1;
  }

  return { byJob, statusByJob, matchCount, latestRunAt };
}
