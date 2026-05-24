import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const qs = searchParams.toString();

  const res = await fetch(
    `${FASTAPI_URL}/api/v1/notifications${qs ? `?${qs}` : ""}`,
    {
      headers: backendProxyHeaders(request),
      cache: "no-store",
    },
  );

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
