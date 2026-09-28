/**
 * An HTTP failure returned by one of the backend APIs.
 *
 * The status code is carried as a field, not only formatted into `message`,
 * because callers have to branch on it: a 404 for one document is a permanent
 * answer and must not be retried, while a 502 from a restarting upstream is
 * worth another attempt. `message` keeps the historical `"{status}: {body}"`
 * shape so `parseApiError` renders these unchanged.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, body: string) {
    super(`${status}: ${body}`);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The fields a 402 licence refusal carries (`detail` of the MCP server's body). */
export interface LicenceRefusalDetail {
  code:
    | "licence_expired"
    | "licence_not_yet_valid"
    | "licence_missing"
    | "licence_invalid"
    | "feature_not_in_plan"
    | "quota_exhausted";
  message: string;
  capability?: "chat" | "generation" | "scan" | null;
  used?: number | null;
  limit?: number | null;
  period_end?: string | null;
  plan?: string | null;
}

/**
 * A request the licence refused (HTTP 402).
 *
 * An `ApiError` so status-based policy keeps working -- `httpStatusFromError`
 * reads 402 and `shouldRetryQuery` never repeats it -- but with the server's
 * sentence as `message` rather than `"402: {json}"`, so every surface that
 * prints an error prints the refusal a user can act on. It is never an auth
 * failure: only a 401 sends the user to login (`api/client.ts`).
 *
 * Lives here, beside `ApiError`, so `lib/licence.ts` and `parseApiError` can
 * both use it without an import cycle.
 */
export class LicenceRefusal extends ApiError {
  readonly code: LicenceRefusalDetail["code"];

  constructor(readonly detail: LicenceRefusalDetail, body: string) {
    super(402, body);
    this.name = "LicenceRefusal";
    this.message = detail.message;
    this.code = detail.code;
  }
}

/**
 * HTTP status behind an error, or null when it is not an HTTP failure.
 *
 * Prefers the typed field and falls back to the `"{status}: ..."` message
 * convention, which is what the Mission Control client (`api/fleet.ts`) and any
 * error that crossed a serialization boundary still carry.
 */
export function httpStatusFromError(err: unknown): number | null {
  if (err instanceof ApiError) return err.status;
  if (err instanceof Error) {
    const match = err.message.match(/(?:^|\s)([1-5]\d\d):\s/);
    if (match) return Number(match[1]);
  }
  return null;
}

export function parseApiError(err: unknown): string {
  if (!(err instanceof Error)) return "An unexpected error occurred";
  if (err instanceof LicenceRefusal) return err.message;
  const match = err.message.match(/^(\d{3}):\s*(.*)$/s);
  if (match) {
    const fallback = `Request failed (${match[1]})`;
    try {
      const body = JSON.parse(match[2]) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) return body.detail.slice(0, 500);
      if (body.detail && typeof body.detail === "object" && !Array.isArray(body.detail)) {
        const message = (body.detail as { message?: unknown }).message;
        if (typeof message === "string" && message.trim()) return message.slice(0, 500);
      }
      if (Array.isArray(body.detail)) {
        const messages = body.detail
          .map((item) => item && typeof item === "object" ? (item as { msg?: unknown }).msg : null)
          .filter((message): message is string => typeof message === "string");
        if (messages.length) return messages.join("; ").slice(0, 500);
      }
    } catch {
      // Non-JSON error bodies stay intentionally opaque.
    }
    return fallback;
  }
  return "Request failed";
}
