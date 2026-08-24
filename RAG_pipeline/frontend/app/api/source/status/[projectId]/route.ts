import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJsonSanitized, safeProjectId } from "../../_proxy";

export const dynamic = "force-dynamic";

/**
 * Authoritative scan/generate state for one project.
 *
 * The pipeline UI needs this because log text is not a reliable completion
 * signal: the project row only flips to `ready` after the scanner has written
 * AND parsed `scan_result.json`, so this is the one source that can gate a
 * generate on a usable scan.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const projectId = safeProjectId((await params).projectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project_id" }, { status: 400 });
  }
  return proxyMcpJsonSanitized(request, `/mcp/status/${projectId}`, { method: "GET" });
}
