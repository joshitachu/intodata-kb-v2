import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { safeProjectId } from "@/app/api/source/_proxy";

interface RouteParams {
  params: Promise<{ projectId: string; ruleId: string }>;
}

/** Digits only: the path segment is interpolated into the upstream URL. */
function safeRuleId(value: string): string | null {
  const trimmed = value.trim();
  return /^[0-9]{1,18}$/.test(trimmed) ? trimmed : null;
}

/**
 * Retire a project rule. Supersede, never hard delete.
 *
 * The rule id is scope-checked upstream against the project in the path, so a
 * manager cannot retire another landscape's rule by guessing a number. This
 * handler only forwards.
 */
export async function DELETE(request: NextRequest, { params }: RouteParams) {
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
    await fastapiFetch(`/api/v1/guidance/projects/${projectId}/rules/${ruleId}`, {
      method: "DELETE",
    });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/projects/[projectId]/rules/[ruleId] DELETE",
      error,
      "Failed to retire the project rule",
    );
  }
}
