import { NextRequest, NextResponse } from "next/server";
import { apiErrorMessage } from "@/lib/apiError";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/bootstrap-admin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const data = await res.json();

    if (!res.ok) {
      return NextResponse.json(
        { error: apiErrorMessage(data.detail, "Bootstrap failed") },
        { status: res.status },
      );
    }

    // Forward Set-Cookie headers from FastAPI so the newly bootstrapped admin
    // is logged in immediately — same pattern as /api/auth/login.
    const response = NextResponse.json(data);
    const setCookies = res.headers.getSetCookie();
    for (const cookie of setCookies) {
      response.headers.append("Set-Cookie", cookie);
    }
    return response;
  } catch {
    return NextResponse.json({ error: "Bootstrap failed" }, { status: 500 });
  }
}
