import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function POST(request: NextRequest, context: { params: Promise<{ projectId: string; packId: string }> }) {
  try {
    const { projectId, packId } = await context.params;
    const body = await request.json();
    if (!body?.manifest || typeof body.manifest !== "object" || Array.isArray(body.manifest)) throw new Error("invalid");
    const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs/projects/${encodeURIComponent(projectId)}/custom/${encodeURIComponent(packId)}/versions`, {
      method: "POST", headers: backendProxyHeaders(request, { json: true }), body: JSON.stringify({ manifest: body.manifest }),
    });
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Custom Pack version could not be saved" }, { status: upstream.status });
  } catch { return NextResponse.json({ error: "Invalid custom Pack" }, { status: 400 }); }
}
