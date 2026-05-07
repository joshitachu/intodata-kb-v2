import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function GET(request: NextRequest) {
  const token = request.nextUrl.searchParams.get("token") || "";
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/auth/invite/validate?token=${encodeURIComponent(token)}`,
    { cache: "no-store" },
  );
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(res.ok ? data : { error: data.detail || "Invalid invite" }, {
    status: res.status,
  });
}
