"use client";

import {
  refreshSession,
  requestSessionRevalidation,
} from "@/lib/session";

/**
 * The one authenticated fetch for browser-side data calls.
 *
 * WHY THIS EXISTS
 *
 * `middleware.ts` deliberately admits a request that carries only a
 * `refresh_token`, because a valid session can always rotate itself into a new
 * access token once the app loads. That leaves a real window in which the page
 * renders, every data query fires, and each one forwards an access token the
 * backend has already expired. The backend answers 401.
 *
 * Before this module each of ~16 hooks called bare `fetch` and turned that 401
 * into a thrown Error. React Query then retried the identical request with the
 * same expired token, and the user saw "No projects available" or "Projects
 * unavailable" until they pressed F5 — which reads as being logged out even
 * though the session was perfectly valid. `useAuth` noticed the 401 on its own
 * `/auth/me` probe and rotated the cookies, but nothing re-drove the queries.
 *
 * WHAT IT DOES
 *
 * On a 401 it rotates the session once (through the single-flight
 * `refreshSession`, so N concurrent 401s cost one rotation) and replays the
 * original request once. A second 401 is surfaced as `SessionExpiredError` and
 * asks `useAuth` to re-probe `/auth/me` — the authoritative source — rather
 * than declaring a logout from a data route's status code.
 */

/** A request failed authorization even with a freshly rotated token. */
export class SessionExpiredError extends Error {
  readonly status = 401;
  constructor(readonly url: string) {
    super("Your session has expired. Sign in again to continue.");
    this.name = "SessionExpiredError";
  }
}

/** The backend answered with an error. Carries the status so callers can branch. */
export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** Set by the proxy layer: false for 401/403/404-style verdicts. */
    readonly retryable: boolean,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

/** True for any failure that repeating the request cannot fix. */
export function isAuthError(error: unknown): boolean {
  return (
    error instanceof SessionExpiredError ||
    (error instanceof ApiRequestError && error.status === 401)
  );
}

/**
 * React Query `retry` default.
 *
 * A 401 must never be retried: the answer is a token rotation, which
 * `authedFetch` has already performed, not a repeat of the same request with
 * the same credential. Other deterministic verdicts (403, 404, 409, 422) do
 * not improve on retry either. Everything else — 5xx, 408, 429, transport —
 * stays retried on the library's default backoff.
 */
export function retryTransientOnly(failureCount: number, error: unknown): boolean {
  if (isAuthError(error)) return false;
  if (error instanceof ApiRequestError && !error.retryable) return false;
  return failureCount < 3;
}

/** A body is only re-sendable if it is not a one-shot stream. */
function isReplayable(init: RequestInit | undefined): boolean {
  const body = init?.body;
  if (body === undefined || body === null) return true;
  return (
    typeof body === "string" ||
    body instanceof FormData ||
    body instanceof URLSearchParams ||
    body instanceof Blob ||
    body instanceof ArrayBuffer
  );
}

/**
 * Call `fetch` with the caller's exact argument list.
 *
 * A wrapper must be transparent: turning `fetch(url)` into
 * `fetch(url, undefined)` is an observable difference for no benefit, and it
 * is enough to break a caller (or a test) that inspects the call. Keeping the
 * arity identical is what lets any hook adopt this layer without its
 * assertions having to know the layer exists.
 */
function passthroughFetch(url: string, init?: RequestInit): Promise<Response> {
  return init === undefined ? fetch(url) : fetch(url, init);
}

/**
 * `fetch`, plus refresh-and-replay on 401.
 *
 * Returns the `Response` so callers keep full control of parsing and of
 * non-401 error handling. Only the auth retry is centralised.
 */
export async function authedFetch(url: string, init?: RequestInit): Promise<Response> {
  const response = await passthroughFetch(url, init);
  if (response.status !== 401) return response;

  // A `ReadableStream` body cannot be sent twice; do not pretend otherwise.
  if (!isReplayable(init)) return response;

  const outcome = await refreshSession();
  if (outcome !== "refreshed") {
    // "unavailable" means we never learned whether the session is valid, so
    // hand back the original response and let the caller's retry policy work.
    if (outcome === "unauthenticated") requestSessionRevalidation();
    return response;
  }

  const replayed = await passthroughFetch(url, init);
  if (replayed.status === 401) {
    // Rotation succeeded and the request is still refused: ask the auth layer
    // to establish the truth from /auth/me.
    requestSessionRevalidation();
    throw new SessionExpiredError(url);
  }
  return replayed;
}

interface FetchJsonOptions extends RequestInit {
  /** Message used when the response carries no usable `error`/`detail`. */
  fallbackError?: string;
}

/**
 * `authedFetch` plus the error-body unwrapping every hook was reimplementing.
 *
 * The proxy layer (`lib/backendProxy.ts`) forwards the upstream status and a
 * `retryable` flag, both of which are preserved on the thrown
 * `ApiRequestError` so `retryTransientOnly` can act on them.
 */
export async function fetchJson<T>(
  url: string,
  { fallbackError = "Request failed", ...init }: FetchJsonOptions = {},
): Promise<T> {
  // Same transparency rule as `passthroughFetch`: a caller that passed no
  // request options must not turn into `fetch(url, {})`.
  const response = await authedFetch(
    url,
    Object.keys(init).length > 0 ? init : undefined,
  );

  if (!response.ok) {
    throw await requestError(response, fallbackError);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Shape the proxy layer (`lib/backendProxy.ts`) puts on an error response. */
interface ProxyErrorBody {
  error?: unknown;
  detail?: unknown;
  retryable?: unknown;
}

/** Read the error body once, for both the message and the retryable verdict. */
async function requestError(response: Response, fallback: string): Promise<ApiRequestError> {
  const text = await response.text().catch(() => "");
  let body: ProxyErrorBody | null = null;
  if (text) {
    try {
      body = JSON.parse(text) as ProxyErrorBody;
    } catch {
      // Not JSON — the generic message is more useful to a user than raw HTML.
    }
  }

  let message = fallback;
  for (const candidate of [body?.error, body?.detail]) {
    if (typeof candidate === "string" && candidate.trim()) {
      message = candidate;
      break;
    }
    if (candidate && typeof candidate === "object") {
      const nested = (candidate as { message?: unknown }).message;
      if (typeof nested === "string" && nested.trim()) {
        message = nested;
        break;
      }
    }
  }

  // The proxy layer states `retryable` explicitly (503 from a transport
  // failure says "try again"); fall back to the status class when it does not.
  const retryable =
    typeof body?.retryable === "boolean"
      ? body.retryable
      : response.status >= 500 || response.status === 408 || response.status === 429;

  return new ApiRequestError(message, response.status, retryable);
}
