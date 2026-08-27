import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJsonSanitized, safeProjectId, safeUuid } from "../../../../../_proxy";

export const dynamic = "force-dynamic";

// Removes one member repository from a multi-repo project. The clone stays on
// disk deliberately (see the server route): it may hold uncommitted state, and
// the next scan ignores it once the row is gone.
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string; sourceId: string }> },
) {
  const { projectId: rawProject, sourceId: rawSource } = await params;
  const projectId = safeProjectId(rawProject);
  const sourceId = safeUuid(rawSource);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }
  if (!sourceId) {
    return NextResponse.json({ error: "Invalid member id" }, { status: 400 });
  }
  return proxyMcpJsonSanitized(
    request,
    `/mcp/multi-repo/projects/${projectId}/members/${sourceId}`,
    { method: "DELETE" },
  );
}
