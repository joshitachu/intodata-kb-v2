import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJson } from "../../../../_proxy";

export const dynamic = "force-dynamic";

// Lists repositories a GitHub App installation can access (for the repo-picker).
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ installationId: string }> },
) {
  const { installationId } = await params;
  if (!/^\d+$/.test(installationId)) {
    return NextResponse.json({ error: "Invalid installation id" }, { status: 400 });
  }
  return proxyMcpJson(request, `/mcp/github/app/installations/${installationId}/repos`, {
    method: "GET",
  });
}
