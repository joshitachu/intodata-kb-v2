import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { safeProjectId } from "@/app/api/source/_proxy";
import { GuidancePreviewResponseSchema } from "@/types/guidance";

interface RouteParams {
  params: Promise<{ projectId: string }>;
}

/**
 * The fully rendered stack, exactly as the agent receives it for this caller.
 *
 * Also the only source of per-layer token consumption: `layer_tokens` against
 * `layer_budgets` is what lets someone see a layer filling up before a write is
 * refused with a 409. Both are declared in the schema for that reason -- an
 * undeclared field here would leave every budget meter permanently blank.
 */
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { projectId: rawProjectId } = await params;
  const projectId = safeProjectId(rawProjectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  try {
    const data = await fastapiFetch(`/api/v1/guidance/projects/${projectId}/preview`);
    return NextResponse.json(GuidancePreviewResponseSchema.parse(data));
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/projects/[projectId]/preview GET",
      error,
      "Failed to render the guidance preview",
    );
  }
}
