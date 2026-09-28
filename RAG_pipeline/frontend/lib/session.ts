"use client";

/**
 * Session primitives shared by the auth hook and the data-fetch layer.
 *
 * They live here rather than in `hooks/useAuth.ts` because both sides need
 * them and neither may own them: `lib/authedFetch.ts` has to rotate the token
 * when a data request comes back 401, and `useAuth` has to hear about a
 * definitive failure so the app can react. Importing one from the other would
 * be a cycle, and a second copy of the refresh call would defeat the
 * single-flight guarantee below.
 */

export type RefreshOutcome = "refreshed" | "unauthenticated" | "unavailable";

/**
 * Module-level so every caller in the tab shares one in-flight request.
 * Refresh-token rotation is single-use: two concurrent POSTs mean one of them
 * presents an already-rotated token and gets a spurious 401.
 *
 * This is also what keeps a cold load cheap. Every data query starting inside
 * the stale-access-token window discovers the 401 independently, but they all
 * await this one promise, so ~20 simultaneous 401s cost exactly one rotation.
 */
let inFlightRefresh: Promise<RefreshOutcome> | null = null;

/**
 * 401 is the only status that means "logged out". Everything else — 429 from a
 * shared rate-limit bucket, 5xx from a restarting or saturated backend, 403
 * from an authorization rule on a perfectly valid session, 402 from a licence
 * that has expired or run out of quota — leaves the session intact.
 *
 * 402 in particular must stay on this side of the line: an expired licence
 * makes the instance view-only, and a user who is logged out cannot view
 * anything. The licence refusal is typed separately (`lib/licence.ts`).
 */
export function classifyAuthFailure(status: number): "unauthenticated" | "unavailable" {
  return status === 401 ? "unauthenticated" : "unavailable";
}

async function requestRefresh(): Promise<RefreshOutcome> {
  try {
    const res = await fetch("/api/auth/refresh", { method: "POST", cache: "no-store" });
    if (res.ok) return "refreshed";
    return classifyAuthFailure(res.status);
  } catch {
    // Network-level failure: the answer is unknown, so assume nothing.
    return "unavailable";
  }
}

/** Rotate the refresh token, collapsing concurrent callers onto one request. */
export function refreshSession(): Promise<RefreshOutcome> {
  if (inFlightRefresh) return inFlightRefresh;
  inFlightRefresh = requestRefresh().finally(() => {
    inFlightRefresh = null;
  });
  return inFlightRefresh;
}

type RevalidationListener = () => void;

const revalidationListeners = new Set<RevalidationListener>();

/** One probe covers every request that failed in the same window. */
const REVALIDATION_THROTTLE_MS = 3_000;
let lastRevalidationRequestedAt = 0;

/**
 * Subscribe to "a request could not be authorized even after a fresh token".
 *
 * The subscriber (`useAuth`) answers by re-probing `/api/auth/me`, which is the
 * only authoritative source of auth state. Deliberately NOT a logout signal: a
 * data route may answer 401 where it meant 403, and treating that as a logout
 * is exactly the class of bug that used to evict valid sessions.
 */
export function onSessionRevalidationNeeded(listener: RevalidationListener): () => void {
  revalidationListeners.add(listener);
  return () => {
    revalidationListeners.delete(listener);
  };
}

export function requestSessionRevalidation(): void {
  const now = Date.now();
  if (now - lastRevalidationRequestedAt < REVALIDATION_THROTTLE_MS) return;
  lastRevalidationRequestedAt = now;
  for (const listener of revalidationListeners) listener();
}

/** Test seam: drops the throttle window so cases do not bleed into each other. */
export function resetSessionRevalidationThrottle(): void {
  lastRevalidationRequestedAt = 0;
}
