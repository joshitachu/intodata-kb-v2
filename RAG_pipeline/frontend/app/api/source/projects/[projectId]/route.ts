import { NextRequest, NextResponse } from "next/server";
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
    const message = error instanceof Error ? error.message : "Project update failed";
    return NextResponse.json({ error: message }, { status: 500 });
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
