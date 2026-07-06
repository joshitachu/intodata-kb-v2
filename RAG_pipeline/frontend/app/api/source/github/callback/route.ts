import { NextRequest, NextResponse } from "next/server";
import { KB_API_URL } from "../../_proxy";

export const dynamic = "force-dynamic";

// Public GitHub App post-install redirect target. GitHub redirects the user's
// browser here (with installation_id + code + state) after they install/authorize.
// On customer domains nginx does not expose /mcp/, so this frontend route forwards
// the request to the MCP callback server-side (carrying the auth cookie), then
// redirects the browser to the Sources page where the repo-picker takes over.
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const cookie = request.headers.get("cookie") || "";

  let upstreamStatus = 0;
  try {
    const upstream = await fetch(
      `${KB_API_URL}/mcp/github/app/callback?${params.toString()}`,
      {
        headers: cookie ? { Cookie: cookie } : {},
        redirect: "manual",
      },
    );
    upstreamStatus = upstream.status;
  } catch {
    upstreamStatus = 502;
  }

  const dest = new URL("/settings/sources", request.nextUrl.origin);
  const installationId = params.get("installation_id");
  const ok = upstreamStatus >= 200 && upstreamStatus < 400;
  if (ok && installationId) {
    dest.searchParams.set("installation_id", installationId);
    dest.searchParams.set("connected", "1");
  } else if (!ok) {
    dest.searchParams.set("connect_error", String(upstreamStatus));
  }
  return NextResponse.redirect(dest);
}
