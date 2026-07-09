import { NextRequest } from "next/server";
import { proxyMcpJson } from "../../_proxy";

export const dynamic = "force-dynamic";

// Lists GitHub App installations already bound to the authenticated Nexus user.
export async function GET(request: NextRequest) {
  return proxyMcpJson(request, "/mcp/github/app/installations", {
    method: "GET",
  });
}
