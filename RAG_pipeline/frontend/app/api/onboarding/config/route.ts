import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function GET(request: NextRequest) {
  const cookieHeader = request.headers.get("cookie") || "";
  const res = await fetch(`${FASTAPI_URL}/api/v1/onboarding/config`, {
    headers: { Cookie: cookieHeader },
    // Config is process-lifetime stable, but we still proxy per-request so
    // cookies work. No caching headers — the client uses `staleTime: Infinity`.
    cache: "no-store",
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
