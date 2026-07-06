import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; containerKey: string }> },
) {
  const { id, containerKey } = await params;
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/sources/${encodeURIComponent(id)}/workspace-map/${encodeURIComponent(containerKey)}`,
    {
      method: "DELETE",
      headers: backendProxyHeaders(request),
      cache: "no-store",
    },
  );
  if (res.status === 204) {
    return new NextResponse(null, { status: 204 });
  }
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
