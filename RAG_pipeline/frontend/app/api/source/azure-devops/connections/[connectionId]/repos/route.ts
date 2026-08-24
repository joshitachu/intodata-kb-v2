import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJson, safeUuid } from "../../../../_proxy";

export const dynamic = "force-dynamic";

// Lists the repositories an Azure DevOps connection can see (for the repo picker).
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ connectionId: string }> },
) {
  const connectionId = safeUuid((await params).connectionId);
  if (!connectionId) {
    return NextResponse.json({ error: "Invalid connection id" }, { status: 400 });
  }
  return proxyMcpJson(request, `/mcp/azure-devops/connections/${connectionId}/repos`, {
    method: "GET",
  });
}
