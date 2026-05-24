import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  const res = await fetch(
    `${FASTAPI_URL}/api/v1/notifications/${id}/read`,
    {
      method: "PUT",
      headers: backendProxyHeaders(request),
    },
  );

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
