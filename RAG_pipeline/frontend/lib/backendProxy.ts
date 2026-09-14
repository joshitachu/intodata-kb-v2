import { NextResponse, type NextRequest } from "next/server";
import { ZodError } from "zod";
import { apiErrorMessage } from "@/lib/apiError";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";
import { BackendResponseError, UpstreamUnavailableError } from "@/lib/upstreamErrors";

/**
 * Shared upstream call for route handlers that proxy to FastAPI.
 *
 * Two rules this exists to enforce:
 *
 * 1. Never swallow the upstream failure. A bare `catch { return 500 }` hides
 *    whether the backend refused the connection, timed out, or answered with an
 *    error — which made the "randomly logged out" bug undiagnosable.
 * 2. Never report a transport failure as a generic 500. `503` says "try again",
 *    and clients must not confuse it with `401` "you are logged out".
 */

/** Long enough for a slow query, short enough to fail before the browser gives up. */
const DEFAULT_TIMEOUT_MS = Number(process.env.BACKEND_TIMEOUT_MS || 20_000);

export { BackendResponseError, UpstreamUnavailableError };

interface FetchBackendOptions extends RequestInit {
  /** Pass `null` to disable the timeout (streaming / long-poll routes). */
  timeoutMs?: number | null;
}

/**
 * Call the backend, converting transport failures into
 * `UpstreamUnavailableError`. A non-2xx *response* is returned as-is — that is
 * the backend's answer, not a transport failure, and the caller decides.
 */
export async function fetchBackend(
  url: string,
  { timeoutMs = DEFAULT_TIMEOUT_MS, ...init }: FetchBackendOptions = {},
): Promise<Response> {
  const controller = timeoutMs === null ? null : new AbortController();
  const timer =
    controller === null ? null : setTimeout(() => controller.abort(), timeoutMs as number);

  try {
    return await fetch(url, { ...init, ...(controller ? { signal: controller.signal } : {}) });
  } catch (error) {
    const timedOut = controller?.signal.aborted === true;
    throw new UpstreamUnavailableError(url, timedOut ? "timeout" : "network", error);
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

/**
 * The shapes `fetch` itself throws when nothing was delivered.
 *
 * Recognised here as well as in `fetchBackend` so a route that still calls
 * `fetch` directly — the routes proxying to the MCP server do, deliberately,
 * because several of them are long-running and must not inherit a timeout —
 * still classifies "the backend is down" as 503 rather than 500.
 */
function transportFailure(error: unknown): "timeout" | "network" | null {
  if (!(error instanceof Error)) return null;
  if (error.name === "AbortError" || error.name === "TimeoutError") return "timeout";
  // undici reports a refused connection / DNS failure as TypeError("fetch failed").
  if (error instanceof TypeError && /fetch failed|network|socket|ECONN|EAI_AGAIN/i.test(error.message)) {
    return "network";
  }
  return null;
}

/**
 * Turn a caught error into a logged, correctly-classified response.
 *
 * Three outcomes, and the distinction between them is the whole point:
 *
 * - `BackendResponseError` — the backend answered. Its status is its verdict
 *   and is forwarded unchanged, so a client can tell "log in again" (401) from
 *   "not allowed" (403) from "already exists" (409). Collapsing these to 500
 *   is what left the browser unable to refresh an expired token: it could not
 *   see that the refusal was an auth refusal.
 * - `UpstreamUnavailableError` — the backend did not answer. `503` says "try
 *   again"; a client must not confuse it with `401` "you are logged out".
 * - anything else — a genuine defect in this handler, so `500`.
 *
 * `fallbackError` is the message used when the backend supplied no usable
 * `detail`; it is never used to *replace* a detail the backend did send.
 */
export function upstreamErrorResponse(
  context: string,
  error: unknown,
  fallbackError = "Request failed",
): NextResponse {
  if (error instanceof BackendResponseError) {
    console.error(`[proxy] ${context}: backend ${error.status} (${error.path})`);
    const detail = error.detail;
    const code =
      detail && typeof detail === "object" && typeof (detail as { code?: unknown }).code === "string"
        ? (detail as { code: string }).code
        : undefined;
    return NextResponse.json(
      {
        error: apiErrorMessage(detail, fallbackError),
        ...(code ? { code } : {}),
        retryable: error.retryable,
      },
      { status: error.status },
    );
  }

  if (error instanceof UpstreamUnavailableError) {
    console.error(
      `[proxy] ${context}: backend ${error.reason} (${error.path})`,
      error.cause instanceof Error ? error.cause.message : error.cause,
    );
    return NextResponse.json(
      { error: "Backend unavailable", reason: error.reason, retryable: true },
      { status: 503, headers: { "Retry-After": "5" } },
    );
  }

  const reason = transportFailure(error);
  if (reason !== null) {
    console.error(
      `[proxy] ${context}: backend ${reason}`,
      error instanceof Error ? error.message : error,
    );
    return NextResponse.json(
      { error: "Backend unavailable", reason, retryable: true },
      { status: 503, headers: { "Retry-After": "5" } },
    );
  }

  console.error(`[proxy] ${context}: unexpected failure`, error);
  return NextResponse.json({ error: fallbackError }, { status: 500 });
}

/**
 * For handlers whose single `try` covers both request validation and the
 * upstream call.
 *
 * Those two failures are not the same answer and must not share one status.
 * Several routes wrapped `Schema.parse(await request.json())` *and* a proxy
 * call in one `catch` that returned `400 { error: error.message }` — so a
 * backend that was simply down told the user their input was invalid, and
 * leaked `fetch failed` / `connect ECONNREFUSED 127.0.0.1:8002` into the
 * browser as the explanation.
 *
 * A schema violation or an unparseable body is the caller's fault (400,
 * message unchanged). Anything else is classified by `upstreamErrorResponse`.
 */
export function badRequestOrUpstreamResponse(
  context: string,
  error: unknown,
  invalidMessage: string,
): NextResponse {
  if (error instanceof ZodError || error instanceof SyntaxError) {
    return NextResponse.json({ error: error.message || invalidMessage }, { status: 400 });
  }
  return upstreamErrorResponse(context, error, invalidMessage);
}

/** Copy Set-Cookie headers from an upstream response onto ours. */
export function forwardSetCookies(from: Response, to: NextResponse): NextResponse {
  for (const cookie of from.headers.getSetCookie()) {
    to.headers.append("Set-Cookie", cookie);
  }
  return to;
}

interface ProxyJsonOptions {
  /** Label used in proxy error logs. */
  context: string;
  /** Message used when the backend supplies no `detail`. */
  fallbackError: string;
  method?: "POST" | "PUT" | "PATCH" | "DELETE" | "GET";
  /** Read the incoming JSON body and forward it. */
  forwardBody?: boolean;
}

/**
 * The standard JSON proxy for auth-adjacent routes.
 *
 * Always forwards Set-Cookie in both directions, including on failures — the
 * backend clears session cookies on a real 401, and dropping those headers
 * leaves stale cookies that keep `middleware.ts` admitting a dead session.
 * Always preserves the upstream status so clients can tell 401 from 429/5xx.
 */
export async function proxyJson(
  request: NextRequest,
  backendPath: string,
  { context, fallbackError, method = "POST", forwardBody = false }: ProxyJsonOptions,
): Promise<NextResponse> {
  try {
    const body = forwardBody ? JSON.stringify(await request.json()) : undefined;
    const res = await fetchBackend(`${FASTAPI_URL}${backendPath}`, {
      method,
      headers: backendProxyHeaders(request, { json: true }),
      ...(body !== undefined ? { body } : {}),
    });

    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;

    if (!res.ok) {
      const detail = data.detail;
      const code =
        detail && typeof detail === "object" && typeof (detail as { code?: unknown }).code === "string"
          ? (detail as { code: string }).code
          : undefined;
      return forwardSetCookies(
        res,
        NextResponse.json(
          {
            error: apiErrorMessage(detail, fallbackError),
            ...(code ? { code } : {}),
            retryable: res.status !== 401 && res.status !== 403,
          },
          { status: res.status },
        ),
      );
    }

    return forwardSetCookies(res, NextResponse.json(data));
  } catch (error) {
    return upstreamErrorResponse(context, error);
  }
}
