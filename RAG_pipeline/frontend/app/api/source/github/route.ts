import { NextRequest, NextResponse } from "next/server";
import { AddGithubSourceSchema } from "@/types";
import { proxyMcpJsonSanitized } from "../_proxy";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

export async function POST(request: NextRequest) {
  try {
    const body = AddGithubSourceSchema.parse(await request.json());
    return proxyMcpJsonSanitized(request, "/mcp/add-repo", {
      method: "POST",
      body: JSON.stringify({
        url: body.repo_url,
        project_id: body.project_id,
        platform: body.platform,
        display_name: body.display_name || undefined,
        github_token: body.github_token || undefined,
        installation_id: body.installation_id || undefined,
      }),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid GitHub source";
    return NextResponse.json({ error: message }, { status: 400 });
  }
}
