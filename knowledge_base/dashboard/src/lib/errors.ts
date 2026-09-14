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
