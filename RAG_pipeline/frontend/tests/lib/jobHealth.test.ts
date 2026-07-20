import { describe, it, expect } from "vitest";
import {
  deriveStatus,
  deriveStatusFromHealth,
  normalizeJobName,
  worstStatus,
  joinHealthToJobs,
} from "@/lib/jobHealth";
import type { RuntimeJobHealth } from "@/types/runtime";

function health(overrides: Partial<RuntimeJobHealth> & { job_name: string }): RuntimeJobHealth {
  return {
    project_id: "reverse_engineer_VW",
    source_key: "demo-topology",
    job_id: `job-${overrides.job_name}`,
    total_runs: 3,
    failed_runs: 0,
    failure_rate: 0,
    current_failure_streak: 0,
    latest_run_at: "2026-07-20T11:00:00Z",
    latest_state: "success",
    ...overrides,
  };
}

describe("deriveStatus", () => {
  it("maps a failed latest run to down (highest priority)", () => {
    expect(deriveStatus({ latestState: "failed", failureRate: 1, currentFailureStreak: 5 })).toBe("down");
  });

  it("maps a high failure rate with a non-failed latest run to degraded", () => {
    expect(deriveStatus({ latestState: "success", failureRate: 0.4, currentFailureStreak: 0 })).toBe("degraded");
  });

  it("maps a failure streak with a non-failed latest run to degraded", () => {
    expect(deriveStatus({ latestState: "success", failureRate: 0, currentFailureStreak: 2 })).toBe("degraded");
  });

  it("prefers degraded over running when the failure rate is high", () => {
    expect(deriveStatus({ latestState: "running", failureRate: 0.5, currentFailureStreak: 0 })).toBe("degraded");
  });

  it("maps a clean running job to running", () => {
    expect(deriveStatus({ latestState: "running", failureRate: 0, currentFailureStreak: 0 })).toBe("running");
  });

  it("maps a clean succeeded job to healthy", () => {
    expect(deriveStatus({ latestState: "success", failureRate: 0.1, currentFailureStreak: 0 })).toBe("healthy");
  });

  it("maps no data / unknown to unknown", () => {
    expect(deriveStatus({ latestState: null, failureRate: 0, currentFailureStreak: 0 })).toBe("unknown");
    expect(deriveStatus({ latestState: "unknown", failureRate: 0, currentFailureStreak: 0 })).toBe("unknown");
  });

  it("threshold boundary: exactly 0.3 is degraded", () => {
    expect(deriveStatusFromHealth(health({ job_name: "j", failure_rate: 0.3 }))).toBe("degraded");
  });
});

describe("normalizeJobName", () => {
  it("lowercases, trims, and strips version suffixes", () => {
    expect(normalizeJobName("  RItem_OnRamp_0.1 ")).toBe("ritem_onramp");
    expect(normalizeJobName("Job_v2")).toBe("job");
    expect(normalizeJobName("Job_3")).toBe("job");
    expect(normalizeJobName("AssetCheck_GetLocation")).toBe("assetcheck_getlocation");
  });
});

describe("worstStatus", () => {
  it("picks the most severe status", () => {
    expect(worstStatus(["healthy", "down", "running"])).toBe("down");
    expect(worstStatus(["healthy", "degraded", "running"])).toBe("degraded");
    expect(worstStatus(["unknown", "healthy"])).toBe("healthy");
    expect(worstStatus([])).toBe("unknown");
  });
});

describe("joinHealthToJobs", () => {
  it("matches on exact name", () => {
    const join = joinHealthToJobs(
      ["AssetCheck_GetLocation"],
      [health({ job_name: "AssetCheck_GetLocation", latest_state: "failed", failure_rate: 0.6, current_failure_streak: 3 })],
    );
    const match = join.byJob.get("AssetCheck_GetLocation")!;
    expect(match.matchKind).toBe("exact");
    expect(match.status).toBe("down");
    expect(join.matchCount).toBe(1);
  });

  it("matches on normalized name (case + version suffix)", () => {
    const join = joinHealthToJobs(
      ["sjItem_OnRamp"],
      [health({ job_name: "sjitem_onramp_0.1", latest_state: "success" })],
    );
    const match = join.byJob.get("sjItem_OnRamp")!;
    expect(match.matchKind).toBe("normalized");
    expect(match.status).toBe("healthy");
  });

  it("matches 1:many on prefix and aggregates worst-status-wins", () => {
    const join = joinHealthToJobs(
      ["rItem_OnRamp"],
      [
        health({ job_name: "rItem_OnRamp_BusinessCentral", latest_state: "failed", failure_rate: 1, current_failure_streak: 1 }),
        health({ job_name: "rItem_OnRamp_Salesforce", latest_state: "success" }),
      ],
    );
    const match = join.byJob.get("rItem_OnRamp")!;
    expect(match.matchKind).toBe("prefix");
    expect(match.matched).toHaveLength(2);
    expect(match.status).toBe("down");
  });

  it("leaves unmatched scan jobs as unknown and excludes them from matchCount", () => {
    const join = joinHealthToJobs(
      ["AssetCheck_Main_Job", "Totally_Different_Runtime_Task"],
      [health({ job_name: "Account_Subscription", latest_state: "success" })],
    );
    // No scan job matches the unrelated runtime row.
    expect(join.byJob.get("AssetCheck_Main_Job")!.status).toBe("unknown");
    expect(join.byJob.get("AssetCheck_Main_Job")!.matchKind).toBe("none");
    expect(join.matchCount).toBe(0);
    expect(join.statusByJob.get("Totally_Different_Runtime_Task")).toBe("unknown");
  });

  it("reports the freshest latest_run_at across matched rows", () => {
    const join = joinHealthToJobs(
      ["jobA"],
      [
        health({ job_name: "jobA", latest_run_at: "2026-07-20T09:00:00Z" }),
        health({ job_name: "jobA", latest_run_at: "2026-07-20T12:30:00Z" }),
      ],
    );
    expect(join.latestRunAt).toBe("2026-07-20T12:30:00Z");
  });
});
