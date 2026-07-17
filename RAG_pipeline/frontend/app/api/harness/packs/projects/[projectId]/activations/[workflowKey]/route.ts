import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest, context: { params: Promise<{ projectId: string; workflowKey: string }> }) {
  const { projectId, workflowKey } = await context.params;
  const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs/projects/${encodeURIComponent(projectId)}/activations/${encodeURIComponent(workflowKey)}`, {
    headers: backendProxyHeaders(request),
  });
  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Pack activation could not be loaded" }, { status: upstream.status });
}

export async function PUT(request: NextRequest, context: { params: Promise<{ projectId: string; workflowKey: string }> }) {
  try {
    const { projectId, workflowKey } = await context.params;
    const body = await request.json();
    const keys = ["foundation_pack_ids", "functional_pack_ids", "style_pack_ids"] as const;
    if (!keys.every((key) => Array.isArray(body?.[key]) && body[key].every((id: unknown) => typeof id === "string"))) throw new Error("invalid");
    const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs/projects/${encodeURIComponent(projectId)}/activations/${encodeURIComponent(workflowKey)}`, {
      method: "PUT", headers: backendProxyHeaders(request, { json: true }), body: JSON.stringify(body),
    });
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Pack selection could not be saved" }, { status: upstream.status });
  } catch { return NextResponse.json({ error: "Invalid Pack selection" }, { status: 400 }); }
}
