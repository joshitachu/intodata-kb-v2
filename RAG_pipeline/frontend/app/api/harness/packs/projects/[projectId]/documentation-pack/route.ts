import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  const upstream = await fetch(
    `${FASTAPI_URL}/api/v1/harness/packs/projects/${encodeURIComponent(projectId)}/documentation-pack`,
    { headers: backendProxyHeaders(request) },
  );
  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Documentation packs could not be loaded" }, { status: upstream.status });
}

export async function PUT(request: NextRequest, context: { params: Promise<{ projectId: string }> }) {
  try {
    const { projectId } = await context.params;
    const body = await request.json();
    if (body?.plugin_pack_id !== null && (!Number.isInteger(body?.plugin_pack_id) || body.plugin_pack_id < 1)) throw new Error("invalid");
    const upstream = await fetch(
      `${FASTAPI_URL}/api/v1/harness/packs/projects/${encodeURIComponent(projectId)}/documentation-pack`,
      { method: "PUT", headers: backendProxyHeaders(request, { json: true }), body: JSON.stringify({ plugin_pack_id: body.plugin_pack_id }) },
    );
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Documentation pack selection could not be saved" }, { status: upstream.status });
  } catch {
    return NextResponse.json({ error: "Invalid documentation pack selection" }, { status: 400 });
  }
}
