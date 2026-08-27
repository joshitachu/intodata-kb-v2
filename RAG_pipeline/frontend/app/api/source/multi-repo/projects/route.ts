import { NextRequest, NextResponse } from "next/server";
import { CreateMultiRepoProjectSchema } from "@/types";
import { proxyMcpJsonSanitized } from "../../_proxy";

export const dynamic = "force-dynamic";
// Cloning N repositories is the slow part of this request. The single-repo route
// allows 300s for one clone; a multi-repo create does the same work per member,
// so it needs the ceiling the platform allows.
export const maxDuration = 800;

// Registers a project spanning several repositories and clones every member.
export async function POST(request: NextRequest) {
  try {
    const body = CreateMultiRepoProjectSchema.parse(await request.json());
    // Every credential reference must be forwarded: /mcp/multi-repo/projects
    // falls back to an unauthenticated clone when neither is present, which a
    // private repo rejects as an authentication failure — a confusing error for
    // what is really a dropped field.
    return proxyMcpJsonSanitized(request, "/mcp/multi-repo/projects", {
      method: "POST",
      body: JSON.stringify({
        project_id: body.project_id,
        display_name: body.display_name || undefined,
        platform: body.platform,
        provider: body.provider,
        members: body.members,
        installation_id: body.installation_id || undefined,
        azure_devops_connection_id: body.azure_devops_connection_id || undefined,
      }),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid multi-repo project";
    return NextResponse.json({ error: message }, { status: 400 });
  }
}
