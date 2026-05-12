import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJson, proxyMcpJsonSanitized, safeProjectId } from "../../../_proxy";

export const dynamic = "force-dynamic";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const projectId = safeProjectId((await params).projectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project_id" }, { status: 400 });
  }
  return proxyMcpJsonSanitized(request, `/mcp/projects/${projectId}/github-webhook/register`, {
    method: "POST",
  });
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const projectId = safeProjectId((await params).projectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project_id" }, { status: 400 });
  }
  return proxyMcpJson(request, `/mcp/projects/${projectId}/github-webhook`, {
    method: "DELETE",
  });
}
