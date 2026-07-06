import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/sources${request.nextUrl.search}`,
    { headers: backendProxyHeaders(request), cache: "no-store" },
  );

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function POST(request: NextRequest) {
  const body = await request.text();
  const res = await fetch(`${FASTAPI_URL}/api/v1/runtime/sources`, {
    method: "POST",
    headers: {
      ...backendProxyHeaders(request),
      "content-type": request.headers.get("content-type") ?? "application/json",
    },
    body,
    cache: "no-store",
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
