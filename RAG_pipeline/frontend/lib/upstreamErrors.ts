/**
 * Typed failures for calls this server makes to the backend.
 *
 * These live in their own leaf module because both halves of the proxy need
 * them: `lib/fastapi.ts` throws them and `lib/backendProxy.ts` turns them into
 * responses. Importing either from the other would be a cycle.
 *
 * Why typed at all: `fastapiFetch` used to throw a bare `Error` whose only
 * record of the upstream status was the text `FastAPI 401: ...`. Route handlers
 * then either flattened every failure to a generic 500 or sniffed the status
 * back out with `message.includes("403")`. Both are lossy — a 401 that reaches
 * the browser as a 500 cannot trigger a token refresh, and a transport failure
 * that reaches it as a 500 cannot be retried.
 */

/** The backend never answered: connection refused, DNS, timeout, abort. */
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

/**
 * The backend answered, and its answer was an error. Its status is a verdict
 * about the request (401 = log in again, 403 = not allowed, 409 = conflict)
 * and must reach the client intact.
 *
 * The message keeps the historical `FastAPI {status}: {body}` shape so any
 * caller still reading the text sees exactly what it saw before.
 */
export class BackendResponseError extends Error {
  constructor(
    readonly status: number,
    /** Raw response body, as text. */
    readonly body: string,
    readonly path: string,
  ) {
    super(`FastAPI ${status}: ${body}`);
    this.name = "BackendResponseError";
  }

  /** Parsed `detail` from a FastAPI error body, when there is one. */
  get detail(): unknown {
    try {
      return (JSON.parse(this.body) as { detail?: unknown }).detail;
    } catch {
      return undefined;
    }
  }

  /**
   * Whether retrying the identical request could plausibly succeed.
   * A 401 is excluded on purpose: it is fixed by refreshing the session, not
   * by repeating the request with the same expired token.
   */
  get retryable(): boolean {
    return this.status >= 500 || this.status === 408 || this.status === 429;
  }
}
