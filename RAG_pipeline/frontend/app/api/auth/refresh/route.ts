import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: "Token refresh failed" },
        { status: res.status },
      );
    }

    const data = await res.json();
    const response = NextResponse.json(data);
    const setCookies = res.headers.getSetCookie();
    for (const cookie of setCookies) {
      response.headers.append("Set-Cookie", cookie);
    }
    return response;
  } catch {
    return NextResponse.json(
      { error: "Token refresh failed" },
      { status: 500 },
    );
  }
}
