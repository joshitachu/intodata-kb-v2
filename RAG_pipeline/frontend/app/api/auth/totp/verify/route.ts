import { NextRequest, NextResponse } from "next/server";
import { apiErrorMessage } from "@/lib/apiError";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

function csrfHeaders(request: NextRequest): HeadersInit {
  const cookieHeader = request.headers.get("cookie") || "";
  const csrfToken = request.cookies.get("csrf_token")?.value;
  return {
    "Content-Type": "application/json",
    ...(cookieHeader ? { Cookie: cookieHeader } : {}),
    ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
  };
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/totp/verify`, {
      method: "POST",
      headers: csrfHeaders(request),
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      return NextResponse.json(
        { error: apiErrorMessage(data.detail, "TOTP verify failed") },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "TOTP verify failed" }, { status: 500 });
  }
}
