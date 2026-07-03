import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/jobs${request.nextUrl.search}`,
    { headers: backendProxyHeaders(request), cache: "no-store" },
  );

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
