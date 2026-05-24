import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/logout`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
    });

    const response = NextResponse.json({ message: "Logged out" });
    // Forward Set-Cookie (clear) headers from FastAPI
    const setCookies = res.headers.getSetCookie();
    for (const cookie of setCookies) {
      response.headers.append("Set-Cookie", cookie);
    }
    return response;
  } catch {
    return NextResponse.json({ error: "Logout failed" }, { status: 500 });
  }
}
