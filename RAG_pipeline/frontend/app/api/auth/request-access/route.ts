import { NextRequest, NextResponse } from "next/server";
import { apiErrorMessage } from "@/lib/apiError";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/request-access`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(res.ok ? data : { error: apiErrorMessage(data.detail, "Request failed") }, {
      status: res.status,
    });
  } catch {
    return NextResponse.json({ error: "Request failed" }, { status: 500 });
  }
}
