import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import {
  ApiRequestError,
  LicenceRefusal,
  fetchJson,
  isAuthError,
  retryTransientOnly,
} from "@/lib/authedFetch";
import {
  classifyAuthFailure,
  onSessionRevalidationNeeded,
  resetSessionRevalidationThrottle,
} from "@/lib/session";
import {
  capabilityBlockReason,
  licenceRefusalFromBody,
  onLicenceRefused,
  parseLicenceRefusalDetail,
  resetLicenceRefusedThrottle,
  type LicenceStatus,
} from "@/lib/licence";
import { upstreamErrorResponse, upstreamResponseError } from "@/lib/backendProxy";
import { BackendResponseError } from "@/lib/upstreamErrors";
import { POST as streamPost } from "@/app/api/conversations/[id]/messages/stream/route";

/**
 * A 402 is the licence saying "this instance is view-only", never the session
 * saying "you are logged out". The regression this guards against is a user
 * with a perfectly valid session being sent to /login -- where they could not
 * even read the documentation the view-only contract promises -- because a
 * subscription lapsed.
 */

const DETAIL = {
  code: "quota_exhausted",
  message: "All 3 documentation generations for this period have been used.",
  capability: "generation",
  used: 3,
  limit: 3,
  period_end: "2026-10-28T00:00:00Z",
  plan: "basic",
} as const;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("402 is a licence refusal, not an auth failure", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    resetSessionRevalidationThrottle();
    resetLicenceRefusedThrottle();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("classifies 402 as not-unauthenticated in the session layer", () => {
    expect(classifyAuthFailure(402)).toBe("unavailable");
    expect(classifyAuthFailure(401)).toBe("unauthenticated");
  });

  it("throws a typed LicenceRefusal without rotating the session or asking for revalidation", async () => {
    const revalidate = vi.fn();
    const unsubscribe = onSessionRevalidationNeeded(revalidate);
    const refused = vi.fn();
    const unsubscribeRefused = onLicenceRefused(refused);
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(json({ error: DETAIL.message, code: DETAIL.code, detail: DETAIL, retryable: false }, 402));

    const failure = await fetchJson("/api/conversations/1/approvals/x/decision", { method: "POST" }).catch(
      (error: unknown) => error,
    );

    expect(failure).toBeInstanceOf(LicenceRefusal);
    expect((failure as LicenceRefusal).message).toBe(DETAIL.message);
    expect((failure as LicenceRefusal).detail.used).toBe(3);
    // One request: no /api/auth/refresh, no replay.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(revalidate).not.toHaveBeenCalled();
    // The banner is told to re-read the status.
    expect(refused).toHaveBeenCalled();
    expect(isAuthError(failure)).toBe(false);
    expect(retryTransientOnly(0, failure)).toBe(false);
    unsubscribe();
    unsubscribeRefused();
  });

  it("still types a 402 whose body does not match the contract", () => {
    const refusal = licenceRefusalFromBody(402, "<html>payment required</html>");
    expect(refusal).toBeInstanceOf(LicenceRefusal);
    expect(isAuthError(refusal)).toBe(false);
    expect(licenceRefusalFromBody(403, { detail: DETAIL })).toBeNull();
  });

  it("reads the refusal from every body shape a 402 can arrive in", () => {
    expect(parseLicenceRefusalDetail({ detail: DETAIL })?.code).toBe("quota_exhausted");
    expect(parseLicenceRefusalDetail({ error: "x", code: DETAIL.code, detail: DETAIL })?.limit).toBe(3);
    expect(parseLicenceRefusalDetail({ error: JSON.stringify({ detail: DETAIL }) })?.plan).toBe("basic");
    expect(parseLicenceRefusalDetail(DETAIL)?.message).toBe(DETAIL.message);
    expect(parseLicenceRefusalDetail({ detail: "Not found" })).toBeNull();
  });

  it("keeps non-licence 4xx on the ordinary error path", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(json({ error: "Forbidden" }, 403));
    const failure = await fetchJson("/api/x").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiRequestError);
    expect(failure).not.toBeInstanceOf(LicenceRefusal);
  });
});

describe("the proxy layer forwards a 402 intact", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("keeps the structured detail on a thrown BackendResponseError", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const response = upstreamErrorResponse(
      "test",
      new BackendResponseError(402, JSON.stringify({ detail: DETAIL }), "/api/v1/x"),
    );
    expect(response.status).toBe(402);
    const body = await response.json();
    expect(body).toMatchObject({ error: DETAIL.message, code: DETAIL.code, detail: DETAIL, retryable: false });
  });

  it("does not add a detail object for a plain-string detail", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const response = upstreamErrorResponse(
      "test",
      new BackendResponseError(404, JSON.stringify({ detail: "Not found" }), "/api/v1/x"),
    );
    expect(await response.json()).toEqual({ error: "Not found", retryable: false });
  });

  it("forwards a raw upstream Response with status and detail", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const response = await upstreamResponseError("test", json({ detail: DETAIL }, 402));
    expect(response.status).toBe(402);
    expect(parseLicenceRefusalDetail(await response.json())).toEqual(DETAIL);
  });

  it("passes a 402 from the chat stream route through as a refusal, not a wrapped string", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: { ...DETAIL, code: "licence_expired", capability: "chat" } }, 402)));
    const request = new NextRequest("http://localhost/api/conversations/7/messages/stream", {
      method: "POST",
      body: JSON.stringify({ query: "hello" }),
      headers: { "Content-Type": "application/json" },
    });

    const response = await streamPost(request, { params: Promise.resolve({ id: "7" }) });

    expect(response.status).toBe(402);
    const body = (await response.json()) as { error: unknown; code: unknown };
    expect(body.code).toBe("licence_expired");
    expect(body.error).toBe(DETAIL.message);
    expect(parseLicenceRefusalDetail(body)?.capability).toBe("chat");
  });
});

describe("capabilityBlockReason", () => {
  const base: LicenceStatus = {
    state: "valid",
    reason: null,
    plan: "basic",
    features: ["chat", "generation"],
    limits: { generations_per_period: 3 },
    period_start: "2026-09-01T00:00:00Z",
    period_end: "2026-10-01T00:00:00Z",
    days_remaining: 3,
    near_expiry: true,
    enforcement: "enforce",
  };

  it("never blocks when enforcement is off or the status is unknown", () => {
    expect(capabilityBlockReason({ ...base, state: "expired", enforcement: "off" }, "chat")).toBeNull();
    expect(capabilityBlockReason(null, "chat")).toBeNull();
  });

  it("blocks every capability when the licence is not valid", () => {
    const expired = { ...base, state: "expired" as const };
    for (const capability of ["chat", "generation", "scan"] as const) {
      expect(capabilityBlockReason(expired, capability)).toMatch(/expired on 2026-10-01.*view-only/);
    }
  });

  it("blocks a feature outside the plan, but not scans", () => {
    const chatOnly = { ...base, features: ["chat"] };
    expect(capabilityBlockReason(chatOnly, "generation")).toMatch(/does not include documentation generation/);
    expect(capabilityBlockReason(chatOnly, "scan")).toBeNull();
    expect(capabilityBlockReason(chatOnly, "chat")).toBeNull();
  });

  it("blocks generation when the period's quota is used", () => {
    const exhausted = { ...base, usage: { generations_used: 3, generations_limit: 3 } };
    expect(capabilityBlockReason(exhausted, "generation")).toMatch(/All 3 documentation generations/);
    expect(capabilityBlockReason({ ...exhausted, usage: { generations_used: 2, generations_limit: 3 } }, "generation")).toBeNull();
  });
});
