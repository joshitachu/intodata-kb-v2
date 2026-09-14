import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  SessionExpiredError,
  authedFetch,
  fetchJson,
  isAuthError,
  retryTransientOnly,
} from "@/lib/authedFetch";
import {
  onSessionRevalidationNeeded,
  resetSessionRevalidationThrottle,
} from "@/lib/session";

/**
 * The window this exists for: `middleware.ts` deliberately admits a request
 * carrying only a refresh_token, so on a cold load the page renders, every
 * query fires, and each one forwards an access token the backend has already
 * expired. Before this layer each hook turned that 401 into a thrown error and
 * React Query retried with the same dead token, so the user was shown
 * "No projects available" / "Projects unavailable" until they pressed F5 —
 * which reads as being logged out with a perfectly valid session.
 */

const REFRESH_CALL = ["/api/auth/refresh", { method: "POST", cache: "no-store" }] as const;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("authedFetch", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    resetSessionRevalidationThrottle();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rotates the session and replays the request once on a 401", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(json({ projects: [{ id: "a" }], total: 1 }));

    const data = await fetchJson<{ projects: unknown[]; total: number }>("/api/docs/projects");

    expect(data).toEqual({ projects: [{ id: "a" }], total: 1 });
    expect(fetchMock).toHaveBeenNthCalledWith(2, ...REFRESH_CALL);
    // Exactly one replay — not a retry loop.
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("preserves the request method and body across the replay", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(json({ ok: true }));

    await fetchJson("/api/onboarding/step/workspace", {
      method: "PUT",
      body: JSON.stringify({ workspace_name: "acme" }),
    });

    expect(fetchMock.mock.calls[2]).toEqual([
      "/api/onboarding/step/workspace",
      { method: "PUT", body: JSON.stringify({ workspace_name: "acme" }) },
    ]);
  });

  it("surfaces a real auth error when the replay is refused as well", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 }));

    await expect(fetchJson("/api/docs/projects")).rejects.toBeInstanceOf(SessionExpiredError);
  });

  it("asks the auth layer to re-probe instead of declaring a logout itself", async () => {
    // A data route may answer 401 where it meant 403. Only /auth/me is
    // authoritative, so the fetch layer requests a probe rather than evicting
    // the session on the strength of one endpoint's status code.
    const revalidate = vi.fn();
    const unsubscribe = onSessionRevalidationNeeded(revalidate);

    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 }));

    const error = await fetchJson("/api/docs/projects").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).status).toBe(401);
    expect(revalidate).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("does not rotate on a 401 when the refresh endpoint is unreachable", async () => {
    // "unavailable" means we never learned whether the session is valid, so
    // nothing is concluded and the caller's retry policy takes over.
    const revalidate = vi.fn();
    const unsubscribe = onSessionRevalidationNeeded(revalidate);

    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }));

    await expect(fetchJson("/api/docs/projects")).rejects.toBeInstanceOf(ApiRequestError);
    expect(revalidate).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("collapses concurrent 401s onto a single token rotation", async () => {
    // Refresh-token rotation is single-use: a second in-flight POST presents an
    // already-rotated token and gets a spurious 401. ~20 queries hitting the
    // stale-token window must therefore cost exactly one rotation.
    const attempts = new Map<string, number>();
    let refreshes = 0;

    vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        refreshes += 1;
        return new Response(null, { status: 200 });
      }
      const n = (attempts.get(url) ?? 0) + 1;
      attempts.set(url, n);
      return n === 1 ? new Response(null, { status: 401 }) : json({ url });
    });

    const results = await Promise.all([
      fetchJson<{ url: string }>("/api/docs/projects"),
      fetchJson<{ url: string }>("/api/projects"),
      fetchJson<{ url: string }>("/api/onboarding/status"),
    ]);

    expect(results.map((r) => r.url)).toEqual([
      "/api/docs/projects",
      "/api/projects",
      "/api/onboarding/status",
    ]);
    expect(refreshes).toBe(1);
  });

  it("leaves a non-401 failure entirely alone", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(json({ error: "Backend unavailable", retryable: true }, 503));

    const error = await fetchJson("/api/docs/projects").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).status).toBe(503);
    expect((error as ApiRequestError).retryable).toBe(true);
    expect((error as ApiRequestError).message).toBe("Backend unavailable");
    // No refresh attempt: a 503 is not an auth problem.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("calls fetch with the caller's exact arguments", async () => {
    // A wrapper must be transparent. `fetch(url)` becoming
    // `fetch(url, undefined)` is an observable difference for no benefit, and
    // it is enough to break a caller — or a test — that inspects the call.
    // That difference is what blocked three topology hooks from adopting this
    // layer at all.
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(json({ ok: true }));

    await authedFetch("/api/projects/proj%201/jobs/jobA/detail");
    expect(fetchMock).toHaveBeenCalledWith("/api/projects/proj%201/jobs/jobA/detail");

    fetchMock.mockClear();
    await fetchJson("/api/documents");
    expect(fetchMock).toHaveBeenCalledWith("/api/documents");
  });

  it("does not replay a request whose body cannot be sent twice", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(null, { status: 401 }));

    const stream = new ReadableStream();
    const response = await authedFetch("/api/upload", { method: "POST", body: stream });

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports the proxy's message rather than a generic one", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(json({ detail: "Project not found" }, 404));

    const error = await fetchJson("/api/docs/nope", {
      fallbackError: "Failed to fetch docs",
    }).catch((e: unknown) => e);

    expect((error as ApiRequestError).message).toBe("Project not found");
  });
});

describe("retryTransientOnly", () => {
  it("never retries an expired session", () => {
    // The fix for a 401 is a token rotation, which authedFetch has already
    // performed. Retrying repeats the request with the same dead credential.
    expect(retryTransientOnly(0, new SessionExpiredError("/api/docs/projects"))).toBe(false);
    expect(retryTransientOnly(0, new ApiRequestError("nope", 401, false))).toBe(false);
  });

  it("never retries a deterministic verdict", () => {
    for (const status of [403, 404, 409, 422]) {
      expect(retryTransientOnly(0, new ApiRequestError("nope", status, false))).toBe(false);
    }
  });

  it("keeps retrying genuinely transient failures", () => {
    expect(retryTransientOnly(0, new ApiRequestError("down", 503, true))).toBe(true);
    expect(retryTransientOnly(2, new ApiRequestError("down", 503, true))).toBe(true);
    expect(retryTransientOnly(3, new ApiRequestError("down", 503, true))).toBe(false);
    expect(retryTransientOnly(0, new TypeError("fetch failed"))).toBe(true);
  });
});

describe("isAuthError", () => {
  it("recognises only auth failures", () => {
    expect(isAuthError(new SessionExpiredError("/x"))).toBe(true);
    expect(isAuthError(new ApiRequestError("x", 401, false))).toBe(true);
    expect(isAuthError(new ApiRequestError("x", 403, false))).toBe(false);
    expect(isAuthError(new Error("x"))).toBe(false);
  });
});
