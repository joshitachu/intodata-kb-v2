import { NextRequest, NextResponse } from "next/server";
import { CreateMultiRepoProjectSchema } from "@/types";
import { proxyMcpJsonSanitized, safeProjectId } from "../../../../_proxy";

export const dynamic = "force-dynamic";
// Adding members clones them, so this shares the create route's ceiling.
export const maxDuration = 800;

// Lists the member repositories of a multi-repo project.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const projectId = safeProjectId((await params).projectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }
  return proxyMcpJsonSanitized(
    request,
    `/mcp/multi-repo/projects/${projectId}/members`,
    { method: "GET" },
  );
}

// Adds member repositories to an existing multi-repo project.
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const projectId = safeProjectId((await params).projectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }
  try {
    // The server takes the project id from the path; the body reuses the create
    // schema, so `project_id` is present but ignored. Validating it here keeps
    // one shape for both calls rather than a near-duplicate schema.
    const body = CreateMultiRepoProjectSchema.parse(await request.json());
    return proxyMcpJsonSanitized(
      request,
      `/mcp/multi-repo/projects/${projectId}/members`,
      {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          platform: body.platform,
          provider: body.provider,
          members: body.members,
          installation_id: body.installation_id || undefined,
          azure_devops_connection_id: body.azure_devops_connection_id || undefined,
        }),
      },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid members payload";
    return NextResponse.json({ error: message }, { status: 400 });
  }
}
