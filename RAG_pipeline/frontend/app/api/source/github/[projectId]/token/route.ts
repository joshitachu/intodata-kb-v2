import { NextRequest, NextResponse } from "next/server";
import { UpdateGithubTokenSchema } from "@/types";
import { proxyMcpJsonSanitized, safeProjectId } from "../../../_proxy";

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
    const message = error instanceof Error ? error.message : "Invalid GitHub token";
    return NextResponse.json({ error: message }, { status: 400 });
  }
}
