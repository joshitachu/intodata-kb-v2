import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  const res = await fetch(`${FASTAPI_URL}/api/v1/onboarding/config`, {
    headers: backendProxyHeaders(request),
    // Config is process-lifetime stable, but we still proxy per-request so
    // cookies work. No caching headers — the client uses `staleTime: Infinity`.
    cache: "no-store",
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
