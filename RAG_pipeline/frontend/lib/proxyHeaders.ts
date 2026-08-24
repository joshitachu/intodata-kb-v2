import type { NextRequest } from "next/server";

/**
 * Headers that identify the real client. Every browser request reaches FastAPI
 * through this Next.js server, so without forwarding these the backend sees the
 * frontend container's IP for *every* user. That collapses each
 * `@limiter.limit(...)` bucket (login 5/min, refresh 10/min, ...) into a single
 * quota shared by the whole deployment, and a 429 on /auth/refresh reads as a
 * logout. Requires the frontend's address in the backend's TRUSTED_PROXIES.
 */
const CLIENT_ADDRESS_HEADERS = [
  "x-forwarded-for",
  "x-real-ip",
  "x-forwarded-proto",
  "x-forwarded-host",
] as const;

function forwardedClientHeaders(source: {
  get(name: string): string | null | undefined;
}): Record<string, string> {
  const forwarded: Record<string, string> = {};
  for (const name of CLIENT_ADDRESS_HEADERS) {
    const value = source.get(name);
    if (value) forwarded[name] = value;
  }
  return forwarded;
}

export function backendProxyHeaders(
  request: NextRequest,
  options: { json?: boolean } = {},
): HeadersInit {
  const cookieHeader = request.headers.get("cookie") || "";
  const csrfToken = request.cookies.get("csrf_token")?.value;

  return {
    ...(options.json ? { "Content-Type": "application/json" } : {}),
    ...forwardedClientHeaders(request.headers),
    ...(cookieHeader ? { Cookie: cookieHeader } : {}),
    ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
  };
}

/** Same client-address forwarding for callers that only have a Headers object. */
export function clientAddressHeaders(headers: Headers): Record<string, string> {
  return forwardedClientHeaders(headers);
}
