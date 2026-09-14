import { NextRequest, NextResponse } from "next/server";
import { AddGithubSourceSchema } from "@/types";
import { proxyMcpJsonSanitized } from "../_proxy";
import { badRequestOrUpstreamResponse } from "@/lib/backendProxy";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

export async function POST(request: NextRequest) {
  try {
    const body = AddGithubSourceSchema.parse(await request.json());
    // Every credential reference the schema accepts must be forwarded. Dropping
    // one here does not fail loudly: /mcp/add-repo falls back to an
    // unauthenticated clone, which private repositories reject as "Authentication
    // failed" — a credential error for what is really a lost field.
    return proxyMcpJsonSanitized(request, "/mcp/add-repo", {
      method: "POST",
      body: JSON.stringify({
        url: body.repo_url,
        project_id: body.project_id,
        platform: body.platform,
        display_name: body.display_name || undefined,
        branch: body.branch || undefined,
        github_token: body.github_token || undefined,
        installation_id: body.installation_id || undefined,
        azure_devops_connection_id: body.azure_devops_connection_id || undefined,
      }),
    });
  } catch (error) {
    return badRequestOrUpstreamResponse("source/github POST", error, "Invalid GitHub source");
  }
}
