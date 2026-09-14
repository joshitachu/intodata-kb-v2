import { NextRequest, NextResponse } from "next/server";
import { ConnectAzureDevOpsSchema } from "@/types";
import { proxyMcpJson, proxyMcpJsonSanitized } from "../../_proxy";
import { badRequestOrUpstreamResponse } from "@/lib/backendProxy";

export const dynamic = "force-dynamic";

// Lists the caller's Azure DevOps organization connections. Tokens are never returned.
export async function GET(request: NextRequest) {
  return proxyMcpJson(request, "/mcp/azure-devops/connections", { method: "GET" });
}

// Connects an organization with a PAT. The token is validated here for shape only;
// MCP verifies it against Azure DevOps before storing it encrypted.
export async function POST(request: NextRequest) {
  try {
    const body = ConnectAzureDevOpsSchema.parse(await request.json());
    return proxyMcpJsonSanitized(request, "/mcp/azure-devops/connections", {
      method: "POST",
      body: JSON.stringify({
        organization_url: body.organization_url,
        pat: body.pat,
      }),
    });
  } catch (error) {
    return badRequestOrUpstreamResponse("source/azure-devops/connections POST", error, "Invalid Azure DevOps connection");
  }
}
