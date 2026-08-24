import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJson, safeUuid } from "../../../_proxy";

export const dynamic = "force-dynamic";

// Removes an Azure DevOps connection and unbinds the projects that used it.
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ connectionId: string }> },
) {
  const connectionId = safeUuid((await params).connectionId);
  if (!connectionId) {
    return NextResponse.json({ error: "Invalid connection id" }, { status: 400 });
  }
  return proxyMcpJson(request, `/mcp/azure-devops/connections/${connectionId}`, {
    method: "DELETE",
  });
}
