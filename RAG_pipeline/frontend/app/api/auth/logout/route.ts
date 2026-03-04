import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  try {
    // Forward cookies to FastAPI
    const cookieHeader = request.headers.get("cookie") || "";

    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/logout`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: cookieHeader,
      },
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
