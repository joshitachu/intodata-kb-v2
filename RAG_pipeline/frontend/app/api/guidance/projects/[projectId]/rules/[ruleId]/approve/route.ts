import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { safeProjectId } from "@/app/api/source/_proxy";
import { GuidanceRuleSchema } from "@/types/guidance";

interface RouteParams {
  params: Promise<{ projectId: string; ruleId: string }>;
}

/** Digits only: the path segment is interpolated into the upstream URL. */
function safeRuleId(value: string): string | null {
  const trimmed = value.trim();
  return /^[0-9]{1,18}$/.test(trimmed) ? trimmed : null;
}

/**
 * Promote a proposed rule to active.
 *
 * Approval is the moment a learned rule starts costing tokens on every turn, so
 * the backend re-runs the budget check and can still answer 409. That status
 * reaches the browser intact through `upstreamErrorResponse`.
 */
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { projectId: rawProjectId, ruleId: rawRuleId } = await params;
  const projectId = safeProjectId(rawProjectId);
  const ruleId = safeRuleId(rawRuleId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }
  if (!ruleId) {
    return NextResponse.json({ error: "Invalid rule id" }, { status: 400 });
  }

  try {
    const data = await fastapiFetch(
      `/api/v1/guidance/projects/${projectId}/rules/${ruleId}/approve`,
      { method: "POST" },
    );
    return NextResponse.json(GuidanceRuleSchema.parse(data));
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/projects/[projectId]/rules/[ruleId]/approve POST",
      error,
      "Failed to approve the proposed rule",
    );
  }
}
