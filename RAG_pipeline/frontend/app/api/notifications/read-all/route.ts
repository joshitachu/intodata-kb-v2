import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function PUT(request: NextRequest) {
  const res = await fetch(`${FASTAPI_URL}/api/v1/notifications/read-all`, {
    method: "PUT",
    headers: backendProxyHeaders(request),
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
