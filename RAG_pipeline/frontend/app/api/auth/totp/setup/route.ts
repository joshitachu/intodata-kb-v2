import { NextRequest, NextResponse } from "next/server";
import { apiErrorMessage } from "@/lib/apiError";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/totp/setup`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
    });
    const data = await res.json();
    if (!res.ok) {
      return NextResponse.json(
        { error: apiErrorMessage(data.detail, "TOTP setup failed") },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "TOTP setup failed" }, { status: 500 });
  }
}
