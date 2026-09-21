import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { safeProjectId } from "@/app/api/source/_proxy";
import { ProjectWorkspaceResponseSchema } from "@/types/guidance";

interface RouteParams {
  params: Promise<{ projectId: string }>;
}

/**
 * The agent's worktree for this project, file by file.
 *
 * This is the only view of what the agent actually receives as a filesystem
 * rather than as an abstraction over one, so every published field is declared
 * in `ProjectWorkspaceResponseSchema`: Zod strips what a schema does not name,
 * and an omitted `always_loaded` would arrive as `undefined`, read as false, and
 * silently under-report the per-turn cost of the entire workspace.
 *
 * Project access is enforced upstream. `upstreamErrorResponse` forwards the
 * verdict unchanged so a 403 stays a 403 rather than becoming an empty tree,
 * which would read as "this project has no workspace" -- a materially wrong
 * answer to the question this view exists to answer.
 */
export async function GET(_request: NextRequest, { params }: RouteParams) {
  const { projectId: rawProjectId } = await params;
  const projectId = safeProjectId(rawProjectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  try {
    const data = await fastapiFetch(`/api/v1/guidance/projects/${projectId}/workspace`);
    return NextResponse.json(ProjectWorkspaceResponseSchema.parse(data));
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/projects/[projectId]/workspace GET",
      error,
      "Failed to load the project workspace",
    );
  }
}
