// Pure runtime-health domain logic for the topology overlay (S3).
//
// Two responsibilities, both deterministic and unit-tested (no React, no I/O):
//   1. deriveStatus() - turn a RuntimeJobHealth aggregate into a coarse
//      operational status, respecting what the row's `liveness` says the
//      numbers MEAN.
//   2. joinHealthToJobs() - the honest identity join between scanner design
//      names and runtime rows. Matching is EXACT-ONLY (on the runtime row's
//      artifact/design name first, then on the scheduled task name), with a
//      narrow case+version normalisation as the last tier. Everything
//      unmatched stays `unknown`. Health is NEVER fabricated.

import type { RuntimeExecutionState, RuntimeJobHealth, RuntimeLiveness } from "@/types/runtime";

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
  /**
   * What the counters MEAN for this row. Optional: a backend that does not yet
   * publish it, or a hand-built literal that omits it, keeps the historical
   * execution semantics exactly.
   */
  liveness?: RuntimeLiveness | null;
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

/**
 * A DEPLOYMENT row, not a run row.
 *
 * `running` is the steady state of a deployed route or data service: it means
 * "deployed and up", not "busy right now", so it is the HEALTHY answer rather
 * than the transient one. Verified: 68 of Pluripharm's route rows sit in
 * `running`, and the execution rule reads every one of them as not-healthy.
 *
 * The failure-rate and streak rules are deliberately NOT applied here: for a
 * lifecycle row the denominator is a redeploy count, so "3 of 10 deployments
 * failed" says something about release history, not about whether the thing is
 * up now. `success` / `terminated` mean the deployment ENDED -- which we cannot
 * distinguish from a deliberate undeploy, so the honest answer is `unknown`
 * (grey), never `healthy` (green).
 */
function deriveLifecycleStatus(latestState: RuntimeExecutionState | null | undefined): JobStatus {
  if (latestState && DOWN_STATES.has(latestState)) return "down";
  if (latestState === "running") return "healthy";
  return "unknown";
}

export function deriveStatus(input: DeriveStatusInput): JobStatus {
  const { latestState, failureRate, currentFailureStreak, liveness } = input;
  if (liveness === "lifecycle") return deriveLifecycleStatus(latestState);
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
    // Deliberately NOT inferred from `executable_kind`: which kinds are
    // lifecycle-shaped is a backend fact about the vendor API, and guessing it
    // here would be a second, drifting copy of that rule.
    liveness: row.liveness ?? null,
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

/**
 * How a scan name reached a runtime row. Every value is an EXACT equality on
 * one of the two identifiers the runtime row carries, modulo case and a
 * trailing version tag.
 *
 * There is deliberately no prefix tier. Measured on the live Pluripharm estate
 * the old `startsWith` tier produced 25 wrong matches, and wrong is worse than
 * absent here: `DWH_Facts_001` was painted with `DWH_Facts_Master`'s health,
 * and `DWH_Facts_Master` was the job that was actually failing, so a healthy
 * job was rendered red and a reader was sent to the wrong place. Others of the
 * same shape: a `_old` copy inheriting the live job's health
 * (`Daily_Queries_6_uur_old`), a `_copy` inheriting the original's
 * (`WMS_IE_stock_mirror_copy`), and `PC_Run_001_JDE` matching two candidates
 * with no way to choose.
 */
export type JobHealthMatchKind =
  | "artifact_name"
  | "artifact_name_normalized"
  | "exact"
  | "normalized"
  | "none";

export interface JobHealthMatch {
  status: JobStatus;
  /** The runtime rows that mapped to this scan job (1:many for TMC deploys). */
  matched: RuntimeJobHealth[];
  /** How the match was made, for transparency/debugging. */
  matchKind: JobHealthMatchKind;
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

function pushIndexed(
  index: Map<string, RuntimeJobHealth[]>,
  key: string,
  row: RuntimeJobHealth,
): void {
  const bucket = index.get(key);
  if (bucket) bucket.push(row);
  else index.set(key, [row]);
}

/**
 * Join scanner design/job names to runtime health rows.
 *
 * Tier order, first non-empty tier wins and its rows aggregate
 * worst-status-wins:
 *   1. exact equality with the row's `artifact_name` (the DESIGN the row was
 *      deployed from -- the identifier a published topology's artifact
 *      annotations actually carry);
 *   2. exact equality with the row's `job_name` (the scheduled task name);
 *   3/4. the same two, after case-folding and stripping a trailing version tag.
 *
 * No match -> `unknown`. An absent `artifact_name` (older backend) simply makes
 * tiers 1 and 3 empty, so the behaviour degrades to the task-name join.
 */
export function joinHealthToJobs(
  scanJobNames: readonly string[],
  healthRows: readonly RuntimeJobHealth[],
): JobHealthJoin {
  const byArtifact = new Map<string, RuntimeJobHealth[]>();
  const byArtifactNormalized = new Map<string, RuntimeJobHealth[]>();
  const byExact = new Map<string, RuntimeJobHealth[]>();
  const byNormalized = new Map<string, RuntimeJobHealth[]>();
  for (const row of healthRows) {
    const artifact = row.artifact_name?.trim();
    if (artifact) {
      pushIndexed(byArtifact, artifact, row);
      pushIndexed(byArtifactNormalized, normalizeJobName(artifact), row);
    }
    pushIndexed(byExact, row.job_name, row);
    pushIndexed(byNormalized, normalizeJobName(row.job_name), row);
  }

  const byJob = new Map<string, JobHealthMatch>();
  const statusByJob = new Map<string, JobStatus>();
  let matchCount = 0;
  let latestRunAt: string | null = null;

  for (const scanJob of scanJobNames) {
    const scanNorm = normalizeJobName(scanJob);
    const tiers: Array<[JobHealthMatchKind, RuntimeJobHealth[] | undefined]> = [
      ["artifact_name", byArtifact.get(scanJob)],
      ["exact", byExact.get(scanJob)],
      ["artifact_name_normalized", scanNorm ? byArtifactNormalized.get(scanNorm) : undefined],
      ["normalized", scanNorm ? byNormalized.get(scanNorm) : undefined],
    ];
    const hit = tiers.find(([, rows]) => rows && rows.length > 0);

    if (!hit) {
      byJob.set(scanJob, { status: "unknown", matched: [], matchKind: "none", latestRunAt: null });
      statusByJob.set(scanJob, "unknown");
      continue;
    }

    const [matchKind, matchedRows] = hit;
    const matched = matchedRows!;
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
