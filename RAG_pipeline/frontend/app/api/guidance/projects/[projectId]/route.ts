import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { safeProjectId } from "@/app/api/source/_proxy";
import {
  GuidanceRuleCreateRequestSchema,
  GuidanceRuleListResponseSchema,
  GuidanceRuleSchema,
  GuidanceStatusSchema,
} from "@/types/guidance";

interface RouteParams {
  params: Promise<{ projectId: string }>;
}

/**
 * The project layer: this landscape's rules, vocabulary and evidence weights.
 *
 * Reading needs project access; writing needs `manage_projects` *and* project
 * access. Both are decided upstream and forwarded here unchanged, including the
 * 409 that says the 600-token ceiling was reached.
 */
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { projectId: rawProjectId } = await params;
  const projectId = safeProjectId(rawProjectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  const requested = new URL(request.url).searchParams.get("status") ?? "active";
  const status = GuidanceStatusSchema.safeParse(requested);
  if (!status.success) {
    return NextResponse.json({ error: "Invalid guidance status" }, { status: 400 });
  }

  try {
    const data = await fastapiFetch(
      `/api/v1/guidance/projects/${projectId}?status=${status.data}`,
    );
    return NextResponse.json(GuidanceRuleListResponseSchema.parse(data));
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/projects/[projectId] GET",
      error,
      "Failed to load project guidance",
    );
  }
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { projectId: rawProjectId } = await params;
  const projectId = safeProjectId(rawProjectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "A JSON body is required" }, { status: 400 });
  }

  const parsed = GuidanceRuleCreateRequestSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: parsed.error.issues[0]?.message ?? "Invalid guidance rule" },
      { status: 400 },
    );
  }

  try {
    const data = await fastapiFetch(`/api/v1/guidance/projects/${projectId}`, {
      method: "POST",
      body: parsed.data,
    });
    return NextResponse.json(GuidanceRuleSchema.parse(data), { status: 201 });
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/projects/[projectId] POST",
      error,
      "Failed to add the project rule",
    );
  }
}
