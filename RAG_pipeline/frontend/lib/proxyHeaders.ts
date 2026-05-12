import type { NextRequest } from "next/server";

export function backendProxyHeaders(
  request: NextRequest,
  options: { json?: boolean } = {},
): HeadersInit {
  const cookieHeader = request.headers.get("cookie") || "";
  const csrfToken = request.cookies.get("csrf_token")?.value;

  return {
    ...(options.json ? { "Content-Type": "application/json" } : {}),
    ...(cookieHeader ? { Cookie: cookieHeader } : {}),
    ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
  };
}
