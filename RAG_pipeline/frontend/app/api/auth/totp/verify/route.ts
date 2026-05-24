import { NextRequest, NextResponse } from "next/server";
import { apiErrorMessage } from "@/lib/apiError";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/totp/verify`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      return NextResponse.json(
        { error: apiErrorMessage(data.detail, "TOTP verify failed") },
        { status: res.status },
      );
    }
    const response = NextResponse.json(data);
    const setCookies = res.headers.getSetCookie();
    for (const cookie of setCookies) {
      response.headers.append("Set-Cookie", cookie);
    }
    return response;
  } catch {
    return NextResponse.json({ error: "TOTP verify failed" }, { status: 500 });
  }
}
