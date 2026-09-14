import { describe, expect, it } from "vitest";
import { queryOutcome, type QueryLike } from "@/lib/queryState";

/**
 * The bug: "No projects available" was the fall-through branch for any state
 * that was neither `isLoading` nor `error`. React Query v5 has such a state —
 * `fetchStatus: "paused"`, where data is undefined, error is null, and
 * `isLoading` is false because `isLoading === isPending && isFetching` and a
 * paused retryer is not fetching. The user was told their account was empty.
 */

function query<T>(over: Partial<QueryLike<T>> = {}): QueryLike<T> {
  return { data: undefined, error: null, fetchStatus: "idle", ...over };
}

describe("queryOutcome", () => {
  it("reports a first load in flight as unknown, not empty", () => {
    expect(queryOutcome(query({ fetchStatus: "fetching" }))).toEqual({
      kind: "unknown",
      phase: "fetching",
    });
  });

  it("reports a PAUSED retry as unknown, not empty", () => {
    // The verified state: attempt 1 failed, and by the time the retry backoff
    // elapsed the tab was unfocused, so the retryer paused instead of
    // retrying. `error` is null because starting a fetch with no data resets
    // it, and the query survives unmount/remount without restarting — so the
    // lie persisted for as long as the tab stayed hidden.
    // `phase` is carried so the affordance can be worded honestly rather
    // than promising progress that is not happening.
    expect(queryOutcome(query({ fetchStatus: "paused" }))).toEqual({
      kind: "unknown",
      phase: "paused",
    });
  });

  it("reports a not-yet-started query as unknown, not empty", () => {
    // `enabled: false` — nothing has been asked, so nothing is known.
    expect(queryOutcome(query({ fetchStatus: "idle" }))).toEqual({
      kind: "unknown",
      phase: "idle",
    });
  });

  it("reports a failure as a failure, distinguishable from empty", () => {
    const error = new Error("Backend unavailable");
    expect(queryOutcome(query({ error }))).toEqual({
      kind: "failed",
      error,
      message: "Backend unavailable",
    });
  });

  it("falls back to the supplied message when the error carries none", () => {
    const outcome = queryOutcome(query({ error: new Error("   ") }), "Could not load projects");
    expect(outcome).toEqual({
      kind: "failed",
      error: expect.any(Error),
      message: "Could not load projects",
    });
  });

  it("only reports emptiness from a real answer", () => {
    const outcome = queryOutcome(query({ data: { projects: [] } }));
    expect(outcome).toEqual({ kind: "ready", data: { projects: [] } });
  });

  it("keeps showing data through a failed background refetch", () => {
    // Cached data plus a refetch error must not blank the UI or claim empty.
    const outcome = queryOutcome(
      query({ data: { projects: ["a"] }, error: new Error("boom"), fetchStatus: "idle" }),
    );
    expect(outcome).toEqual({ kind: "ready", data: { projects: ["a"] } });
  });

  it("treats an undefined error the same as null", () => {
    // `error` is typed `unknown`; a caller passing undefined must not be read
    // as a failure with no message.
    expect(queryOutcome(query({ error: undefined, fetchStatus: "fetching" })).kind).toBe(
      "unknown",
    );
  });
});
