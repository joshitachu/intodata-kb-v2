import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

/**
 * Update a source's non-secret settings (ingest scope, filters, history window).
 *
 * Separate from the credential-rotation route so changing a setting does not
 * force an operator to re-type the API token. The backend rejects any field
 * that is secret or identity-bearing.
 */
export async function PATCH(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await request.text();
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/sources/${encodeURIComponent(id)}/config`,
    {
      method: "PATCH",
      headers: {
        ...backendProxyHeaders(request),
        "content-type": request.headers.get("content-type") ?? "application/json",
      },
      body,
      cache: "no-store",
    },
  );
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
