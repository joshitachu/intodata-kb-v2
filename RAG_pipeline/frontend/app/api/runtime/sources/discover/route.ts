import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

/**
 * Test unsaved source credentials and list the ingest scopes they can reach.
 *
 * The credentials pass through this proxy but are never persisted here: the
 * backend validates them, returns the scope list, and writes nothing.
 */
export async function POST(request: NextRequest) {
  const body = await request.text();
  const res = await fetch(`${FASTAPI_URL}/api/v1/runtime/sources/discover`, {
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
