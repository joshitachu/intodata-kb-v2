import { NextRequest } from "next/server";
import { proxyMcpJson } from "@/app/api/source/_proxy";

/**
 * Pack requests all proxy to the MCP server's `/mcp/plugins` endpoints.
 *
 * `/mcp/*` is not publicly exposed in customer deployments, so every pack call
 * from the browser lands on `/api/packs/*` and is forwarded here with the
 * caller's cookie. The MCP server resolves the user from that cookie, which is
 * what makes a personal pack private -- the browser never states who it is.
 */
export function proxyPacks(
  request: NextRequest,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return proxyMcpJson(request, `/mcp/plugins${path}`, init);
}

/** Forward the JSON body of a mutating request unchanged. */
export async function forwardBody(request: NextRequest): Promise<string> {
  return JSON.stringify(await request.json().catch(() => ({})));
}

export const dynamic = "force-dynamic";
