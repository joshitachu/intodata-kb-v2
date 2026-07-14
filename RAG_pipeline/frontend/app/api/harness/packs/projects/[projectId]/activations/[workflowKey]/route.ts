import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function PUT(request: NextRequest, context: { params: Promise<{ projectId: string; workflowKey: string }> }) {
  try {
    const { projectId, workflowKey } = await context.params;
    const body = await request.json();
    const ids = body?.active_pack_ids;
    if (!Array.isArray(ids) || !ids.every((id) => typeof id === "string")) throw new Error("invalid");
    const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs/projects/${encodeURIComponent(projectId)}/activations/${encodeURIComponent(workflowKey)}`, {
      method: "PUT", headers: backendProxyHeaders(request, { json: true }), body: JSON.stringify({ active_pack_ids: ids }),
    });
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Pack selection could not be saved" }, { status: upstream.status });
  } catch { return NextResponse.json({ error: "Invalid Pack selection" }, { status: 400 }); }
}
