import { NextRequest, NextResponse } from "next/server";

// These routes intentionally remain readable without a Nexus account. Keep this
// list aligned with AuthProvider's public-route handling: middleware runs before
// the app loads, while AuthProvider handles a 401 after client hydration.
const PUBLIC_PATHS = ["/login", "/onboarding", "/guide", "/api/auth", "/api/tenant"];
const MFA_SETUP_PATH = "/settings/security";

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const segment = token.split(".")[1];
    if (!segment) return null;
    const base64 = segment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    return JSON.parse(atob(padded)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  // Allow public paths
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // Allow Next.js internals and static assets
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon") ||
    pathname.includes(".")
  ) {
    return NextResponse.next();
  }

  // A missing access token does not necessarily mean the user is logged out:
  // the client can rotate a valid refresh token into a new access token once
  // the app loads.
  const accessToken = request.cookies.get("access_token");
  const refreshToken = request.cookies.get("refresh_token");
  if (!accessToken && !refreshToken) {
    // Use X-Forwarded headers to build the correct external URL
    const proto = request.headers.get("x-forwarded-proto") || "https";
    const host = request.headers.get("x-forwarded-host") || request.headers.get("host") || "localhost";
    const loginUrl = new URL("/login", `${proto}://${host}`);
    // Keep the query string: third-party return trips (e.g. the GitHub App
    // post-install redirect) carry their whole payload there, and dropping it
    // sends the user back to a URL that can no longer do anything.
    loginUrl.searchParams.set("redirect", `${pathname}${search}`);
    return NextResponse.redirect(loginUrl);
  }

  // Redirect users who have not set up MFA. The mfa_enabled claim is embedded
  // in the access token at issuance so this check requires no DB call.
  if (accessToken && !pathname.startsWith("/api/") && pathname !== MFA_SETUP_PATH) {
    const payload = decodeJwtPayload(accessToken.value);
    if (payload !== null && payload.mfa_enabled === false) {
      const proto = request.headers.get("x-forwarded-proto") || "https";
      const host = request.headers.get("x-forwarded-host") || request.headers.get("host") || "localhost";
      return NextResponse.redirect(new URL(MFA_SETUP_PATH, `${proto}://${host}`));
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
