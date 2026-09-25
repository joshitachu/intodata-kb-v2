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

  // A misfired run never started and a timed-out run was killed mid-flight.
  // Both used to fall through to "unknown", which is how a job whose schedule
  // silently stopped firing still read as fine on the topology overlay.
  it("maps a misfired latest run to down even with a clean failure rate", () => {
    expect(deriveStatus({ latestState: "misfired", failureRate: 0, currentFailureStreak: 0 })).toBe("down");
  });

  it("maps a timed-out latest run to down even with a clean failure rate", () => {
    expect(deriveStatus({ latestState: "timed_out", failureRate: 0, currentFailureStreak: 0 })).toBe("down");
  });

  it("maps a terminated latest run to degraded, never healthy or unknown", () => {
    expect(deriveStatus({ latestState: "terminated", failureRate: 0, currentFailureStreak: 0 })).toBe("degraded");
  });

  it("threshold boundary: exactly 0.3 is degraded", () => {
    expect(deriveStatusFromHealth(health({ job_name: "j", failure_rate: 0.3 }))).toBe("degraded");
  });
});

/**
 * A `lifecycle` row is a DEPLOYMENT that stays up, not a run. `running` means
 * "deployed and up"; the failure-rate and streak counters describe redeploys,
 * not runs, so they must not colour the verdict.
 */
describe("deriveStatus with lifecycle liveness", () => {
  it("reads a running deployment as healthy, not as transient 'running'", () => {
    expect(deriveStatus({ latestState: "running", failureRate: 0, currentFailureStreak: 0, liveness: "lifecycle" })).toBe("healthy");
  });

  it("does not let a redeploy failure rate downgrade a deployment that is up", () => {
    expect(deriveStatus({ latestState: "running", failureRate: 0.9, currentFailureStreak: 7, liveness: "lifecycle" })).toBe("healthy");
  });

  it("reads a failed deployment as down", () => {
    expect(deriveStatus({ latestState: "failed", failureRate: 0, currentFailureStreak: 0, liveness: "lifecycle" })).toBe("down");
  });

  it("reads an ended deployment (success/terminated) as unknown, never healthy", () => {
    expect(deriveStatus({ latestState: "success", failureRate: 0, currentFailureStreak: 0, liveness: "lifecycle" })).toBe("unknown");
    expect(deriveStatus({ latestState: "terminated", failureRate: 0, currentFailureStreak: 0, liveness: "lifecycle" })).toBe("unknown");
  });

  it("keeps execution semantics for execution rows and for an absent liveness", () => {
    expect(deriveStatus({ latestState: "running", failureRate: 0, currentFailureStreak: 0, liveness: "execution" })).toBe("running");
    expect(deriveStatus({ latestState: "running", failureRate: 0, currentFailureStreak: 0, liveness: "unknown" })).toBe("running");
    expect(deriveStatus({ latestState: "running", failureRate: 0, currentFailureStreak: 0 })).toBe("running");
  });

  it("reads liveness off the row in deriveStatusFromHealth", () => {
    expect(deriveStatusFromHealth(health({ job_name: "route", latest_state: "running", liveness: "lifecycle" }))).toBe("healthy");
    expect(deriveStatusFromHealth(health({ job_name: "job", latest_state: "running" }))).toBe("running");
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

  it("aggregates a 1:many exact match worst-status-wins", () => {
    const join = joinHealthToJobs(
      ["rItem_OnRamp"],
      [
        health({ job_name: "rItem_OnRamp", environment_name: "PRD", latest_state: "failed", failure_rate: 1, current_failure_streak: 1 }),
        health({ job_name: "rItem_OnRamp", environment_name: "ACC", latest_state: "success" }),
      ],
    );
    const match = join.byJob.get("rItem_OnRamp")!;
    expect(match.matchKind).toBe("exact");
    expect(match.matched).toHaveLength(2);
    expect(match.status).toBe("down");
  });

  it("prefers the runtime row's artifact (design) name over its scheduled task name", () => {
    const join = joinHealthToJobs(
      ["collector_edifact_sales_orders_c003"],
      [
        health({
          job_name: "PRD_collector_edifact_sales_orders",
          artifact_name: "collector_edifact_sales_orders_c003",
          latest_state: "success",
        }),
      ],
    );
    const match = join.byJob.get("collector_edifact_sales_orders_c003")!;
    expect(match.matchKind).toBe("artifact_name");
    expect(match.status).toBe("healthy");
  });

  it("degrades to the task-name join when the backend sends no artifact_name", () => {
    const join = joinHealthToJobs(["jobA"], [health({ job_name: "jobA", latest_state: "success" })]);
    expect(join.byJob.get("jobA")!.matchKind).toBe("exact");
  });

  /**
   * The prefix tier is GONE, and these four are the verified reason. Measured
   * on the live Pluripharm estate it produced 25 wrong matches; each name below
   * is one real defect it caused, and wrong health is worse than no health.
   */
  describe("no fuzzy prefix tier", () => {
    it("does not give DWH_Facts_001 the health of the different, failing DWH_Facts_Master", () => {
      const join = joinHealthToJobs(
        ["DWH_Facts_001"],
        [health({ job_name: "DWH_Facts_Master", latest_state: "failed", failure_rate: 1, current_failure_streak: 4 })],
      );
      const match = join.byJob.get("DWH_Facts_001")!;
      expect(match.matchKind).toBe("none");
      expect(match.matched).toEqual([]);
      expect(match.status).toBe("unknown");
      expect(join.matchCount).toBe(0);
    });

    it("does not let a dead _old copy inherit the live job's health", () => {
      const join = joinHealthToJobs(
        ["Daily_Queries_6_uur_old"],
        [health({ job_name: "Daily_Queries_6_uur", latest_state: "success" })],
      );
      expect(join.byJob.get("Daily_Queries_6_uur_old")!.matchKind).toBe("none");
      expect(join.byJob.get("Daily_Queries_6_uur_old")!.status).toBe("unknown");
    });

    it("does not let a _copy inherit the original's health", () => {
      const join = joinHealthToJobs(
        ["WMS_IE_stock_mirror_copy"],
        [health({ job_name: "WMS_IE_stock_mirror", latest_state: "success" })],
      );
      expect(join.byJob.get("WMS_IE_stock_mirror_copy")!.matchKind).toBe("none");
    });

    it("does not silently pick one of two ambiguous prefix candidates", () => {
      const join = joinHealthToJobs(
        ["PC_Run_001_JDE"],
        [
          health({ job_name: "PC_Run_001_JDE_Extract", latest_state: "success" }),
          health({ job_name: "PC_Run_001_JDE_Load", latest_state: "failed", failure_rate: 1, current_failure_streak: 2 }),
        ],
      );
      expect(join.byJob.get("PC_Run_001_JDE")!.matchKind).toBe("none");
      expect(join.byJob.get("PC_Run_001_JDE")!.status).toBe("unknown");
    });
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
