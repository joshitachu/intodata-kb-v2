import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJsonSanitized, safeUuid } from "../../../../_proxy";

export const dynamic = "force-dynamic";

// Re-verifies a stored PAT against Azure DevOps. Tokens expire silently, so the UI
// needs a way to distinguish "still connected" from "connected-looking but dead".
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ connectionId: string }> },
) {
  const connectionId = safeUuid((await params).connectionId);
  if (!connectionId) {
    return NextResponse.json({ error: "Invalid connection id" }, { status: 400 });
  }
  return proxyMcpJsonSanitized(
    request,
    `/mcp/azure-devops/connections/${connectionId}/test`,
    { method: "POST" },
  );
}
