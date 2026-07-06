import { NextRequest } from "next/server";
import { proxyMcpJson, safeProjectId } from "../../_proxy";

export const dynamic = "force-dynamic";

// Returns the GitHub App install URL (carrying signed state) to redirect the user to.
export async function GET(request: NextRequest) {
  const raw = request.nextUrl.searchParams.get("project_id");
  let path = "/mcp/github/app/connect";
  if (raw) {
    const projectId = safeProjectId(raw);
    if (projectId) {
      path += `?project_id=${encodeURIComponent(projectId)}`;
    }
  }
  return proxyMcpJson(request, path, { method: "GET" });
}
