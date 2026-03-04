import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  try {
    const cookieHeader = request.headers.get("cookie") || "";

    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: cookieHeader,
      },
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
