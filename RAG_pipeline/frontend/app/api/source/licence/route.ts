import { NextRequest } from "next/server";
import { proxyMcpJsonSanitized } from "../_proxy";

export const dynamic = "force-dynamic";

/**
 * The MCP server's licence status: the authority for scans and generations,
 * and the only status that carries this period's generation usage.
 */
export async function GET(request: NextRequest) {
  return proxyMcpJsonSanitized(request, "/mcp/licence/status", { method: "GET" });
}
