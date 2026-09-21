import { describe, expect, it } from "vitest";
import {
  LabsHealthSchema,
  describeLaneDetail,
  laneVerdict,
} from "@/components/labs/labsHealth";

/**
 * The lane banner used to report the inverse of the truth, and these are the two
 * payloads that proved it: the healthy one threw, the unhealthy one rendered as
 * "responding". Both are the backend's real output, copied from
 * `LabsHealthResponse` -- a fixture invented here would drift with the bug.
 */
const HEALTHY = {
  lane: "labs_opencode",
  opencode_available: true,
  detail: { healthy: true },
  error: null,
  model: "big-pickle",
};

const UNREACHABLE = {
  lane: "labs_opencode",
  opencode_available: false,
  detail: null,
  error: "All connection attempts failed",
  model: "big-pickle",
};

describe("LabsHealthSchema", () => {
  it("accepts the object the probe actually puts in `detail`", () => {
    // The Zod issue this replaced, verbatim from the browser:
    //   {"code":"invalid_type","expected":"string","received":"object",
    //    "path":["detail"]}
    const parsed = LabsHealthSchema.parse(HEALTHY);

    expect(parsed.detail).toEqual({ healthy: true });
    expect(parsed.opencode_available).toBe(true);
  });

  it("accepts the failure payload, error and all", () => {
    expect(LabsHealthSchema.parse(UNREACHABLE).error).toBe(
      "All connection attempts failed",
    );
  });

  it("rejects a 200 that cannot support a verdict", () => {
    // Not widened to make this pass: a body with no `opencode_available` is not
    // this contract, and the banner's error branch says more about it than a
    // fabricated "responding" would.
    expect(() => LabsHealthSchema.parse({ lane: "labs_opencode" })).toThrow();
  });
});

describe("laneVerdict", () => {
  it("calls a reachable lane ready, and names the model it answers with", () => {
    expect(laneVerdict(LabsHealthSchema.parse(HEALTHY))).toEqual({
      tone: "ok",
      label: "Lane ready (big-pickle)",
    });
  });

  it("reports the reason an unreachable lane gave", () => {
    expect(laneVerdict(LabsHealthSchema.parse(UNREACHABLE))).toEqual({
      tone: "down",
      label: "All connection attempts failed",
    });
  });

  it("still says the lane is down when it explained nothing", () => {
    expect(
      laneVerdict(
        LabsHealthSchema.parse({ opencode_available: false, error: "   " }),
      ),
    ).toEqual({ tone: "down", label: "Lane unreachable" });
  });

  it("omits the model rather than inventing one", () => {
    expect(laneVerdict(LabsHealthSchema.parse({ opencode_available: true }))).toEqual({
      tone: "ok",
      label: "Lane ready",
    });
  });
});

describe("describeLaneDetail", () => {
  it("formats the probe body by value shape", () => {
    expect(describeLaneDetail({ healthy: true, version: "1.18.7", uptime: 42 })).toBe(
      "healthy: yes, version: 1.18.7, uptime: 42",
    );
  });

  it("skips a nested value rather than rendering [object Object]", () => {
    expect(describeLaneDetail({ providers: { moonshot: true }, healthy: true })).toBe(
      "healthy: yes",
    );
  });

  it("has nothing to say about an absent or empty body", () => {
    expect(describeLaneDetail(null)).toBeNull();
    expect(describeLaneDetail({})).toBeNull();
    expect(describeLaneDetail({ providers: { moonshot: true } })).toBeNull();
  });
});
