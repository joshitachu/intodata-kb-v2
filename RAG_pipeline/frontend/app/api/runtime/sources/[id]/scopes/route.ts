import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

/** List the ingest scopes a registered source's stored credentials can reach. */
export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/sources/${encodeURIComponent(id)}/scopes`,
    { method: "POST", headers: backendProxyHeaders(request), cache: "no-store" },
  );
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
