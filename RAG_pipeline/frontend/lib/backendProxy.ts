import { NextResponse, type NextRequest } from "next/server";
import { apiErrorMessage } from "@/lib/apiError";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

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

export class UpstreamUnavailableError extends Error {
  constructor(
    readonly path: string,
    readonly reason: "timeout" | "network",
    readonly cause: unknown,
  ) {
    super(`Backend ${reason} on ${path}`);
    this.name = "UpstreamUnavailableError";
  }
}

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
 * Turn a caught error into a logged, correctly-classified response.
 *
 * `503` for anything that means "the backend did not answer" so clients can
 * retry instead of treating it as a terminal failure.
 */
export function upstreamErrorResponse(context: string, error: unknown): NextResponse {
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

  console.error(`[proxy] ${context}: unexpected failure`, error);
  return NextResponse.json({ error: "Request failed" }, { status: 500 });
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
