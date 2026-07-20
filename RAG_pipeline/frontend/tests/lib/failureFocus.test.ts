import { describe, it, expect } from "vitest";
import {
  computeFailureFocusSet,
  isFocusDimmableNode,
  isFocusDimmableEdge,
} from "@/lib/failureFocus";
import { propagateHealth } from "@/lib/healthPropagation";
import type { EffectiveStatus } from "@/lib/healthPropagation";
import type { JobStatus } from "@/lib/jobHealth";

function statusMap(entries: Record<string, JobStatus>): Map<string, JobStatus> {
  return new Map(Object.entries(entries));
}

describe("computeFailureFocusSet", () => {
  it("returns null when there are no down or impacted jobs", () => {
    // healthy + degraded only — degraded is a warning, not part of the cascade.
    const prop = propagateHealth({ A: ["B"] }, statusMap({ A: "healthy", B: "degraded" }));
    expect(computeFailureFocusSet(prop, new Map())).toBeNull();
  });

  it("keeps the down job, its impacted ancestors, and the red-edge endpoints", () => {
    // A calls B, B calls C; C is down => B and A impacted. D is unrelated.
    const prop = propagateHealth(
      { A: ["B"], B: ["C"] },
      statusMap({ A: "healthy", B: "healthy", C: "down", D: "healthy" }),
    );

    const set = computeFailureFocusSet(prop, new Map());
    expect(set).not.toBeNull();
    // The failing cascade nodes are kept.
    expect(set!.has("job:C")).toBe(true); // down
    expect(set!.has("job:B")).toBe(true); // impacted
    expect(set!.has("job:A")).toBe(true); // impacted (transitive)
    // Unrelated healthy job is NOT kept (it will be dimmed).
    expect(set!.has("job:D")).toBe(false);
  });

  it("does not keep degraded (yellow) jobs — they are warnings, not the cascade", () => {
    const prop = propagateHealth(
      { A: ["B"] },
      statusMap({ A: "degraded", B: "healthy" }),
    );
    // No down job => nothing to focus.
    expect(computeFailureFocusSet(prop, new Map())).toBeNull();
  });

  it("keeps collapsed groups whose worst member is down or impacted", () => {
    const prop = propagateHealth({ A: ["B"] }, statusMap({ A: "healthy", B: "down" }));
    const groupWorst = new Map<string, EffectiveStatus>([
      ["grpFail", "down"],
      ["grpImpact", "impacted"],
      ["grpWarn", "degraded"],
      ["grpOk", "healthy"],
    ]);

    const set = computeFailureFocusSet(prop, groupWorst);
    expect(set!.has("group:grpFail")).toBe(true);
    expect(set!.has("group:grpImpact")).toBe(true);
    expect(set!.has("group:grpWarn")).toBe(false);
    expect(set!.has("group:grpOk")).toBe(false);
  });

  it("includes both endpoints of every red failing edge", () => {
    const prop = propagateHealth({ A: ["B"] }, statusMap({ A: "healthy", B: "down" }));
    // A->B is a failing edge; both endpoints must be in the keep set.
    const set = computeFailureFocusSet(prop, new Map());
    expect(set!.has("job:A")).toBe(true);
    expect(set!.has("job:B")).toBe(true);
  });
});

describe("failure-focus dim exemptions", () => {
  it("only job nodes are dimmable; group and connection nodes are exempt", () => {
    expect(isFocusDimmableNode("job:Foo")).toBe(true);
    expect(isFocusDimmableNode("group:grp1")).toBe(false);
    expect(isFocusDimmableNode("conn:MSSQL")).toBe(false);
    expect(isFocusDimmableNode("group-bg:grp1")).toBe(false);
  });

  it("only job->job edges are dimmable; anything touching a group/conn node is exempt", () => {
    expect(isFocusDimmableEdge("job:A", "job:B")).toBe(true); // in-detail call edge
    expect(isFocusDimmableEdge("group:grp1", "conn:MSSQL")).toBe(false); // aggregated group-uses
    expect(isFocusDimmableEdge("group:grp1", "group:grp2")).toBe(false); // inter-group call
    expect(isFocusDimmableEdge("job:A", "conn:MSSQL")).toBe(false); // per-job uses
  });
});
