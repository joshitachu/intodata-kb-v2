import { NextRequest, NextResponse } from "next/server";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { proxyMcpJson, proxyMcpJsonSanitized, safeProjectId } from "../../_proxy";

export const dynamic = "force-dynamic";

interface Params {
  params: Promise<{ projectId: string }>;
}

export async function PATCH(request: NextRequest, { params }: Params) {
  const raw = (await params).projectId;
  const projectId = safeProjectId(decodeURIComponent(raw));
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  try {
    const body = await request.json();
    return proxyMcpJsonSanitized(request, `/mcp/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  } catch (error) {
    return upstreamErrorResponse("source/projects/[projectId] PATCH", error, "Project update failed");
  }
}

export async function DELETE(request: NextRequest, { params }: Params) {
  const raw = (await params).projectId;
  const projectId = safeProjectId(decodeURIComponent(raw));
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  return proxyMcpJson(request, `/mcp/projects/${projectId}`, {
    method: "DELETE",
  });
}
