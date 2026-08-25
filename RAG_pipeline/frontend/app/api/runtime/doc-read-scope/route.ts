import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

/**
 * What a project's documentation runs read from its runtime mirror, and which
 * environments are available to choose from.
 *
 * Read-side companion to the source config PATCH: the picker offers only
 * environments present in the mirror, so a selection cannot resolve to no
 * evidence.
 */
export async function GET(request: NextRequest) {
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/doc-read-scope${request.nextUrl.search}`,
    { headers: backendProxyHeaders(request), cache: "no-store" },
  );
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
