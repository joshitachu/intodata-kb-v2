import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/otp/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        { error: data.detail || "Code verification failed" },
        { status: res.status },
      );
    }
    const response = NextResponse.json(data);
    for (const cookie of res.headers.getSetCookie()) {
      response.headers.append("Set-Cookie", cookie);
    }
    return response;
  } catch {
    return NextResponse.json({ error: "Code verification failed" }, { status: 500 });
  }
}
