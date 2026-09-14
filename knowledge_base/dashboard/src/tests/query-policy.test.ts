import { describe, expect, it } from "vitest";
import { DEFAULT_STALE_MS, createQueryClient, shouldRetryQuery } from "../lib/queryClient";
import { ApiError, httpStatusFromError, parseApiError } from "../lib/errors";

/**
 * The global query defaults are load policy, so they are pinned.
 *
 * A blanket `refetchInterval: 5000` had every mounted query re-fetching
 * continuously -- 339 calls to `/mcp/platforms`, 336 to `/mcp/models`, 334 to
 * `/mcp/plugins` and 343 to `/mcp/logs/{project}` in ~2h of partial use -- and
 * it turned one 404 in the doc viewer into 81 identical failed requests.
 */
describe("global query defaults", () => {
  it("sets no blanket refetch interval", () => {
    const defaults = createQueryClient().getDefaultOptions().queries;
    expect(defaults?.refetchInterval).toBeUndefined();
  });

  it("caches long enough for a mount or a focus not to be a fetch", () => {
    const defaults = createQueryClient().getDefaultOptions().queries;
    expect(defaults?.staleTime).toBe(DEFAULT_STALE_MS);
    expect(DEFAULT_STALE_MS).toBeGreaterThanOrEqual(30_000);
  });
});

describe("shouldRetryQuery", () => {
  it("never retries a 404 -- a missing document is an answer", () => {
    expect(shouldRetryQuery(0, new ApiError(404, '{"detail":"File not found"}'))).toBe(false);
  });

  it("never retries any other 4xx", () => {
    for (const status of [400, 403, 409, 422]) {
      expect(shouldRetryQuery(0, new ApiError(status, "no"))).toBe(false);
    }
  });

  it("retries a 5xx a bounded number of times", () => {
    expect(shouldRetryQuery(0, new ApiError(502, "bad gateway"))).toBe(true);
    expect(shouldRetryQuery(1, new ApiError(502, "bad gateway"))).toBe(true);
    expect(shouldRetryQuery(2, new ApiError(502, "bad gateway"))).toBe(false);
  });

  it("retries a network failure, which carries no status", () => {
    expect(shouldRetryQuery(0, new TypeError("Failed to fetch"))).toBe(true);
  });
});

describe("ApiError", () => {
  it("carries the status as a field", () => {
    expect(new ApiError(404, "nope").status).toBe(404);
  });

  it("keeps the message shape parseApiError already reads", () => {
    const err = new ApiError(422, '{"detail":"Elasticsearch endpoint is required"}');
    expect(parseApiError(err)).toBe("Elasticsearch endpoint is required");
  });

  it("recovers a status from the message convention when the type was lost", () => {
    // Mission Control's client (`api/fleet.ts`) throws a plain Error.
    expect(httpStatusFromError(new Error("Mission Control 503: unavailable"))).toBe(503);
    expect(httpStatusFromError(new Error("500: proxy failure"))).toBe(500);
    expect(httpStatusFromError(new Error("no status here"))).toBeNull();
    expect(httpStatusFromError("not an error")).toBeNull();
  });
});
