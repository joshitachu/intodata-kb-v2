import { NextRequest, NextResponse } from "next/server";
import { KB_API_URL } from "../../_proxy";
import { FASTAPI_URL } from "@/lib/fastapi";

export const dynamic = "force-dynamic";

// Public GitHub App post-install redirect target. GitHub redirects the user's
// browser here (with installation_id + code + state) after they install/authorize.
// On customer domains nginx does not expose /mcp/, so this frontend route forwards
// the request to the MCP callback server-side (carrying the auth cookie), then
// redirects the browser to the Sources page where the repo-picker takes over.
const MAX_DETAIL_LENGTH = 300;

/** Read FastAPI's `detail` from an error response, bounded for a URL. */
async function readDetail(upstream: Response): Promise<string> {
  try {
    const text = (await upstream.text()).slice(0, 2000);
    let message = text;
    try {
      const parsed: unknown = JSON.parse(text);
      const detail =
        parsed && typeof parsed === "object" && "detail" in parsed
          ? (parsed as { detail: unknown }).detail
          : null;
      // A JSON body with no string detail carries nothing worth showing.
      message = typeof detail === "string" ? detail : "";
    } catch {
      // Not JSON (proxy error page, plain text) — pass the body through.
    }
    return message.trim().slice(0, MAX_DETAIL_LENGTH);
  } catch {
    return "";
  }
}

/** Overlay Set-Cookie values onto a request Cookie header. */
function mergeCookies(original: string, setCookies: string[]): string {
  const jar = new Map<string, string>();
  const put = (pair: string) => {
    const [name, ...value] = pair.trim().split("=");
    if (name) jar.set(name, value.join("="));
  };
  original.split(";").filter(Boolean).forEach(put);
  setCookies.map((raw) => raw.split(";")[0]).forEach(put);
  return [...jar].map(([name, value]) => `${name}=${value}`).join("; ");
}

/**
 * Rotate the session server-side and return the cookies to use / forward.
 *
 * An access token lives 60 minutes; installing a GitHub App easily takes longer
 * than whatever is left of it. The browser survives that (the client refreshes
 * on demand, and middleware lets a request with only a refresh token through),
 * but this route is a server-to-server hop with no client in the loop: an
 * expired access token simply came back as 401 "Not authenticated" and the whole
 * connect died. Refresh here, then retry.
 *
 * The rotated refresh token MUST reach the browser, otherwise it keeps a token
 * this call has just revoked and the next request logs the user out.
 */
async function refreshSession(
  cookie: string,
): Promise<{ cookie: string; setCookies: string[] } | null> {
  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers: cookie ? { Cookie: cookie } : {},
    });
    if (!res.ok) return null;
    const setCookies = res.headers.getSetCookie?.() ?? [];
    if (setCookies.length === 0) return null;
    return { cookie: mergeCookies(cookie, setCookies), setCookies };
  } catch {
    return null;
  }
}

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const incomingCookie = request.headers.get("cookie") || "";

  const callMcp = (cookie: string) =>
    fetch(`${KB_API_URL}/mcp/github/app/callback?${params.toString()}`, {
      headers: cookie ? { Cookie: cookie } : {},
      redirect: "manual",
    });

  let upstreamStatus = 0;
  let upstreamDetail = "";
  let rotatedCookies: string[] = [];
  try {
    let upstream = await callMcp(incomingCookie);
    if (upstream.status === 401) {
      const refreshed = await refreshSession(incomingCookie);
      if (refreshed) {
        rotatedCookies = refreshed.setCookies;
        upstream = await callMcp(refreshed.cookie);
      }
    }
    upstreamStatus = upstream.status;
    if (upstreamStatus >= 400) {
      // Carry the reason to the UI. A bare status code ("connect_error=400") is
      // undiagnosable for the operator who has to fix the App configuration.
      upstreamDetail = await readDetail(upstream);
      if (upstreamStatus === 401 && !upstreamDetail) {
        upstreamDetail = "Your Nexus session expired while GitHub was installing the App. Sign in and connect again.";
      }
    }
  } catch {
    upstreamStatus = 502;
    upstreamDetail = "Nexus could not reach the MCP server to finish the connection.";
  }

  // Redirect with a RELATIVE Location. Behind nginx/Docker the request URL seen
  // by Next.js is the container-internal one (e.g. 0.0.0.0:3000), so building an
  // absolute URL from request.nextUrl.origin sends the browser to a host that
  // does not exist. A relative Location is resolved by the browser against the
  // public URL it actually requested, which is correct for every proxy setup.
  const query = new URLSearchParams();
  const installationId = params.get("installation_id");
  const ok = upstreamStatus >= 200 && upstreamStatus < 400;
  if (ok && installationId) {
    query.set("installation_id", installationId);
    query.set("connected", "1");
  } else if (!ok) {
    query.set("connect_error", String(upstreamStatus));
    if (upstreamDetail) query.set("connect_message", upstreamDetail);
  }
  const search = query.toString();
  const response = new NextResponse(null, {
    status: 303,
    headers: { Location: `/settings/sources${search ? `?${search}` : ""}` },
  });
  for (const cookie of rotatedCookies) {
    response.headers.append("Set-Cookie", cookie);
  }
  return response;
}
