import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  const projectId = request.nextUrl.searchParams.get("project_id");
  const workflowKey = request.nextUrl.searchParams.get("workflow_key");
  const query = new URLSearchParams();
  if (projectId) query.set("project_id", projectId);
  if (workflowKey) query.set("workflow_key", workflowKey);
  const suffix = query.size ? `?${query.toString()}` : "";
  const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs${suffix}`, {
    headers: backendProxyHeaders(request),
  });
  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Packs could not be loaded" }, { status: upstream.status });
}
