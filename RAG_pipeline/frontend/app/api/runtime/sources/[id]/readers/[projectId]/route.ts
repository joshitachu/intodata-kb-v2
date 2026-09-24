import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

/** Stop a project from reading this source's mirror. */
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; projectId: string }> },
) {
  const { id, projectId } = await params;
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/sources/${encodeURIComponent(id)}/readers/${encodeURIComponent(projectId)}`,
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
