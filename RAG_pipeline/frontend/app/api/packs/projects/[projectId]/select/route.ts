import { NextRequest } from "next/server";
import { proxyMcpJson } from "@/app/api/source/_proxy";

export const dynamic = "force-dynamic";

/**
 * Select the pack a project generates with.
 *
 * This one endpoint lives under `/mcp/projects/{id}/plugin` rather than
 * `/mcp/plugins/...`, so it does not use the shared pack proxy.
 */
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const { projectId } = await params;
  const body = JSON.stringify(await request.json().catch(() => ({})));
  return proxyMcpJson(request, `/mcp/projects/${encodeURIComponent(projectId)}/plugin`, {
    method: "PATCH",
    body,
  });
}
