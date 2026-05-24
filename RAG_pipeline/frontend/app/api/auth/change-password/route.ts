import { NextRequest, NextResponse } from "next/server";
import { apiErrorMessage } from "@/lib/apiError";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/change-password`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      return NextResponse.json(
        { error: apiErrorMessage(data.detail, "Password change failed") },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "Password change failed" }, { status: 500 });
  }
}
