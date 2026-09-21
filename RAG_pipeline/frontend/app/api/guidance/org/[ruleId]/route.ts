import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

interface RouteParams {
  params: Promise<{ ruleId: string }>;
}

/** Digits only: the path segment is interpolated into the upstream URL. */
function safeRuleId(value: string): string | null {
  const trimmed = value.trim();
  return /^[0-9]{1,18}$/.test(trimmed) ? trimmed : null;
}

/**
 * Retire a house rule. Owner-only upstream, and a supersede rather than a
 * delete -- the row keeps its text so an answer given last month stays
 * explainable.
 */
export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const { ruleId: rawRuleId } = await params;
  const ruleId = safeRuleId(rawRuleId);
  if (!ruleId) {
    return NextResponse.json({ error: "Invalid rule id" }, { status: 400 });
  }

  try {
    await fastapiFetch(`/api/v1/guidance/org/${ruleId}`, { method: "DELETE" });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/org/[ruleId] DELETE",
      error,
      "Failed to retire the organization rule",
    );
  }
}
