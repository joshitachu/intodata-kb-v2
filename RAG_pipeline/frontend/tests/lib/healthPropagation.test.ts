import { describe, it, expect } from "vitest";
import { propagateHealth, callEdgeKey } from "@/lib/healthPropagation";
import type { JobStatus } from "@/lib/jobHealth";

function statusMap(entries: Record<string, JobStatus>): Map<string, JobStatus> {
  return new Map(Object.entries(entries));
}

describe("propagateHealth", () => {
  it("propagates a down child up to every transitive caller as impacted", () => {
    // A calls B, B calls C. C is down. A and B are healthy on their own.
    const jobCalls = { A: ["B"], B: ["C"] };
    const status = statusMap({ A: "healthy", B: "healthy", C: "down" });

    const { jobs, failingEdges } = propagateHealth(jobCalls, status);

    expect(jobs.get("C")!.effectiveStatus).toBe("down");
    expect(jobs.get("C")!.hopDistance).toBe(0);

    // B is directly above the down seed, A is two hops up.
    expect(jobs.get("B")!.effectiveStatus).toBe("impacted");
    expect(jobs.get("B")!.ownStatus).toBe("healthy");
    expect(jobs.get("B")!.hopDistance).toBe(1);
    expect(jobs.get("B")!.impactedVia).toEqual(["C"]);

    expect(jobs.get("A")!.effectiveStatus).toBe("impacted");
    expect(jobs.get("A")!.ownStatus).toBe("healthy");
    expect(jobs.get("A")!.hopDistance).toBe(2);
    expect(jobs.get("A")!.impactedVia).toEqual(["B", "C"]);

    // Every edge on the failing path is flagged.
    expect(failingEdges.has(callEdgeKey("B", "C"))).toBe(true);
    expect(failingEdges.has(callEdgeKey("A", "B"))).toBe(true);
    expect(failingEdges.size).toBe(2);
  });

  it("preserves a job's own status while marking it impacted", () => {
    const jobCalls = { parent: ["child"] };
    const status = statusMap({ parent: "degraded", child: "down" });
    const { jobs } = propagateHealth(jobCalls, status);
    expect(jobs.get("parent")!.ownStatus).toBe("degraded");
    expect(jobs.get("parent")!.effectiveStatus).toBe("impacted");
  });

  it("keeps a down parent as down rather than impacted", () => {
    const jobCalls = { parent: ["child"] };
    const status = statusMap({ parent: "down", child: "down" });
    const { jobs, failingEdges } = propagateHealth(jobCalls, status);
    expect(jobs.get("parent")!.effectiveStatus).toBe("down");
    // The dependency edge is still on a failing path.
    expect(failingEdges.has(callEdgeKey("parent", "child"))).toBe(true);
  });

  it("does not flag jobs with no down descendant", () => {
    const jobCalls = { A: ["B"] };
    const status = statusMap({ A: "healthy", B: "healthy" });
    const { jobs, failingEdges } = propagateHealth(jobCalls, status);
    expect(jobs.get("A")!.effectiveStatus).toBe("healthy");
    expect(jobs.get("A")!.hopDistance).toBeNull();
    expect(failingEdges.size).toBe(0);
  });

  it("is cycle-safe (tRunJob cycles do not loop forever)", () => {
    // A <-> B cycle, plus C calls A. A is down.
    const jobCalls = { A: ["B"], B: ["A"], C: ["A"] };
    const status = statusMap({ A: "down", B: "healthy", C: "healthy" });
    const { jobs, failingEdges } = propagateHealth(jobCalls, status);
    expect(jobs.get("A")!.effectiveStatus).toBe("down");
    expect(jobs.get("B")!.effectiveStatus).toBe("impacted");
    expect(jobs.get("C")!.effectiveStatus).toBe("impacted");
    expect(failingEdges.has(callEdgeKey("B", "A"))).toBe(true);
    expect(failingEdges.has(callEdgeKey("C", "A"))).toBe(true);
  });

  it("assigns the minimum hop distance across multiple down seeds", () => {
    // A calls B and C; B calls D (down); C is also down. A is one hop from C.
    const jobCalls = { A: ["B", "C"], B: ["D"] };
    const status = statusMap({ A: "healthy", B: "healthy", C: "down", D: "down" });
    const { jobs } = propagateHealth(jobCalls, status);
    expect(jobs.get("A")!.hopDistance).toBe(1); // via C directly, not via B->D (2)
    expect(jobs.get("A")!.impactedVia).toEqual(["C"]);
  });

  it("treats jobs with no runtime status as unknown, not down", () => {
    const jobCalls = { A: ["B"] };
    const status = statusMap({ A: "healthy" }); // B absent
    const { jobs, failingEdges } = propagateHealth(jobCalls, status);
    expect(jobs.get("B")!.ownStatus).toBe("unknown");
    expect(jobs.get("B")!.effectiveStatus).toBe("unknown");
    expect(failingEdges.size).toBe(0);
  });
});
