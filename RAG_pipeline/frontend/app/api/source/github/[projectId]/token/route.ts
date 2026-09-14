import { NextRequest, NextResponse } from "next/server";
import { UpdateGithubTokenSchema } from "@/types";
import { proxyMcpJsonSanitized, safeProjectId } from "../../../_proxy";
import { badRequestOrUpstreamResponse } from "@/lib/backendProxy";

export const dynamic = "force-dynamic";

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  try {
    const projectId = safeProjectId((await params).projectId);
    if (!projectId) {
      return NextResponse.json({ error: "Invalid project_id" }, { status: 400 });
    }
    const body = UpdateGithubTokenSchema.parse(await request.json());
    return proxyMcpJsonSanitized(request, `/mcp/projects/${projectId}/github-token`, {
      method: "PATCH",
      body: JSON.stringify({ github_token: body.github_token }),
    });
  } catch (error) {
    return badRequestOrUpstreamResponse("source/github/[projectId]/token PATCH", error, "Invalid GitHub token");
  }
}
