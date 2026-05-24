import { NextRequest, NextResponse } from "next/server";
import { apiErrorMessage } from "@/lib/apiError";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  const { token } = await request.json().catch(() => ({ token: "" }));
  const res = await fetch(`${FASTAPI_URL}/api/v1/auth/invite/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(res.ok ? data : { error: apiErrorMessage(data.detail, "Invalid invite") }, {
    status: res.status,
  });
}
