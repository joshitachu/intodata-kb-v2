import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { id } = await params;
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/incidents/${encodeURIComponent(id)}/evidence${request.nextUrl.search}`,
    { headers: backendProxyHeaders(request), cache: "no-store" },
  );

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
