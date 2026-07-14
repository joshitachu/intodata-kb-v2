import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs/projects/${encodeURIComponent(projectId)}/recommendations`, { headers: backendProxyHeaders(request) });
  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Pack recommendations could not be loaded" }, { status: upstream.status });
}
