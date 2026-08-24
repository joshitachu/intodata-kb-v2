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
