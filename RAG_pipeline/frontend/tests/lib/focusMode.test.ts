import { describe, it, expect } from "vitest";
import { computeFocus } from "@/lib/focusMode";

describe("computeFocus", () => {
  it("single focal node with no neighbours: only itself (+ its own connections)", () => {
    const result = computeFocus(
      "Solo",
      {},
      {},
      { Solo: ["MSSQL"] },
      2,
    );
    expect([...result.inFocusJobs]).toEqual(["Solo"]);
    expect(result.inFocus.has("job:Solo")).toBe(true);
    expect(result.inFocus.has("conn:MSSQL")).toBe(true);
    expect(result.boundaries.size).toBe(0);
  });

  it("chain A->B->C->D->E, focus A, hop=2: only A,B,C in focus; C is a downstream boundary", () => {
    const jobCalls = { A: ["B"], B: ["C"], C: ["D"], D: ["E"] };
    const jobCalledBy = { B: ["A"], C: ["B"], D: ["C"], E: ["D"] };
    const result = computeFocus("A", jobCalls, jobCalledBy, {}, 2);

    expect([...result.inFocusJobs].sort()).toEqual(["A", "B", "C"]);
    // D and E are beyond the horizon.
    expect(result.inFocus.has("job:D")).toBe(false);
    expect(result.inFocus.has("job:E")).toBe(false);
    // C sits at hop 2 and still calls D (hidden) -> boundary downstream = 1.
    expect(result.boundaries.get("C")).toEqual({ upstream: 0, downstream: 1 });
    // A and B are strictly inside the horizon -> not boundaries.
    expect(result.boundaries.has("A")).toBe(false);
    expect(result.boundaries.has("B")).toBe(false);
  });

  it("chain focus in the middle includes ±2 in both directions", () => {
    const jobCalls = { A: ["B"], B: ["C"], C: ["D"], D: ["E"] };
    const jobCalledBy = { B: ["A"], C: ["B"], D: ["C"], E: ["D"] };
    // Focus C, hop 2: ancestors B,A ; descendants D,E -> all five in focus.
    const result = computeFocus("C", jobCalls, jobCalledBy, {}, 2);
    expect([...result.inFocusJobs].sort()).toEqual(["A", "B", "C", "D", "E"]);
    // A has no caller, E has no callee -> no hidden neighbours anywhere.
    expect(result.boundaries.size).toBe(0);
  });

  it("chain focus in the middle with a longer tail marks the horizon jobs as boundaries", () => {
    // A->B->C->D->E->F->G ; focus D hop 2 -> B,C,D,E,F in focus.
    const jobCalls = { A: ["B"], B: ["C"], C: ["D"], D: ["E"], E: ["F"], F: ["G"] };
    const jobCalledBy = { B: ["A"], C: ["B"], D: ["C"], E: ["D"], F: ["E"], G: ["F"] };
    const result = computeFocus("D", jobCalls, jobCalledBy, {}, 2);
    expect([...result.inFocusJobs].sort()).toEqual(["B", "C", "D", "E", "F"]);
    // B (hop 2 up) still has caller A hidden -> upstream boundary.
    expect(result.boundaries.get("B")).toEqual({ upstream: 1, downstream: 0 });
    // F (hop 2 down) still calls G hidden -> downstream boundary.
    expect(result.boundaries.get("F")).toEqual({ upstream: 0, downstream: 1 });
  });

  it("branch: multiple callers and callees are all included at hop=1", () => {
    const jobCalls = { Focal: ["Out1", "Out2", "Out3"] };
    const jobCalledBy = { Focal: ["In1", "In2"] };
    const result = computeFocus("Focal", jobCalls, jobCalledBy, {}, 1);
    expect([...result.inFocusJobs].sort()).toEqual([
      "Focal",
      "In1",
      "In2",
      "Out1",
      "Out2",
      "Out3",
    ]);
    expect(result.boundaries.size).toBe(0);
  });

  it("branch beyond horizon: counts hidden fan-out at the boundary node", () => {
    // Focal calls Mid ; Mid calls three leaves. hop=1 -> only Focal, Mid.
    const jobCalls = { Focal: ["Mid"], Mid: ["L1", "L2", "L3"] };
    const jobCalledBy = { Mid: ["Focal"], L1: ["Mid"], L2: ["Mid"], L3: ["Mid"] };
    const result = computeFocus("Focal", jobCalls, jobCalledBy, {}, 1);
    expect([...result.inFocusJobs].sort()).toEqual(["Focal", "Mid"]);
    expect(result.boundaries.get("Mid")).toEqual({ upstream: 0, downstream: 3 });
  });

  it("cross-group: neighbourhood is group-agnostic (a caller in another group is included)", () => {
    // Focus is defined purely on call structure; group membership is irrelevant
    // here — the caller from a different group is still an ancestor.
    const jobCalls = {};
    const jobCalledBy = { GetLocation: ["Main_Job"] };
    const result = computeFocus("GetLocation", jobCalls, jobCalledBy, { GetLocation: ["LogDB"] }, 2);
    expect(result.inFocusJobs.has("Main_Job")).toBe(true);
    expect(result.inFocus.has("job:Main_Job")).toBe(true);
    expect(result.inFocus.has("conn:LogDB")).toBe(true);
  });

  it("an interior parent with hidden cousins is NOT a boundary (reverse_engineer_VW shape)", () => {
    // Focus GetLocation; its only ancestor Main_Job (1 hop up) also calls five
    // SIBLINGS of GetLocation. Those cousins sit off any pure ancestor/descendant
    // chain from GetLocation, so increasing the horizon never reveals them —
    // Main_Job must therefore carry NO "+N downstream" badge, and the cousins
    // stay out of focus.
    const jobCalls = {
      AssetCheck_Main_Job: [
        "AssetCheck_SendServiceRequest",
        "AssetCheck_GetLocations",
        "AssetCheck_GetLocation",
        "AssetCheck_SendWorkOrderUpdate",
        "AssetCheck_GetWorkOrders",
        "AssetCheck_GetWorkOrder",
      ],
    };
    const jobCalledBy = {
      AssetCheck_SendServiceRequest: ["AssetCheck_Main_Job"],
      AssetCheck_GetLocations: ["AssetCheck_Main_Job"],
      AssetCheck_GetLocation: ["AssetCheck_Main_Job"],
      AssetCheck_SendWorkOrderUpdate: ["AssetCheck_Main_Job"],
      AssetCheck_GetWorkOrders: ["AssetCheck_Main_Job"],
      AssetCheck_GetWorkOrder: ["AssetCheck_Main_Job"],
    };
    const result = computeFocus(
      "AssetCheck_GetLocation",
      jobCalls,
      jobCalledBy,
      { AssetCheck_GetLocation: ["LogConnection"] },
      2,
    );

    // Only the focal + its impacted parent are in focus.
    expect([...result.inFocusJobs].sort()).toEqual([
      "AssetCheck_GetLocation",
      "AssetCheck_Main_Job",
    ]);
    // The five cousins are hidden.
    expect(result.inFocus.has("job:AssetCheck_GetWorkOrder")).toBe(false);
    expect(result.inFocus.has("job:AssetCheck_SendServiceRequest")).toBe(false);
    // Main_Job is interior (ancestorDist 1, not the horizon 2) -> no badge.
    expect(result.boundaries.size).toBe(0);
    // The focal's own connection is present.
    expect(result.inFocus.has("conn:LogConnection")).toBe(true);
  });

  it("disconnected nodes are excluded", () => {
    const jobCalls = { A: ["B"] };
    const jobCalledBy = { B: ["A"] };
    // C and D exist elsewhere in the manifest but are unreachable from A.
    const result = computeFocus("A", jobCalls, jobCalledBy, { C: ["X"], D: ["Y"] }, 3);
    expect([...result.inFocusJobs].sort()).toEqual(["A", "B"]);
    expect(result.inFocus.has("job:C")).toBe(false);
    expect(result.inFocus.has("conn:X")).toBe(false);
  });

  it("is cycle-safe (A->B->C->A) and terminates", () => {
    const jobCalls = { A: ["B"], B: ["C"], C: ["A"] };
    const jobCalledBy = { B: ["A"], C: ["B"], A: ["C"] };
    const result = computeFocus("A", jobCalls, jobCalledBy, {}, 10);
    expect([...result.inFocusJobs].sort()).toEqual(["A", "B", "C"]);
    // Whole cycle is in focus, so nothing is hidden.
    expect(result.boundaries.size).toBe(0);
  });
});
