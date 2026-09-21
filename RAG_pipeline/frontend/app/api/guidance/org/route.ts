import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import {
  GuidanceRuleCreateRequestSchema,
  GuidanceRuleListResponseSchema,
  GuidanceRuleSchema,
  GuidanceStatusSchema,
} from "@/types/guidance";

/**
 * The organization layer: house rules that bind every project and every user.
 *
 * Read is open to any authenticated caller and write is Owner-only; both gates
 * live in the backend. This handler forwards the verdict unchanged
 * (`upstreamErrorResponse` preserves 403 and 409) so the browser can tell "not
 * allowed" from "the layer is full" rather than seeing one generic failure.
 */
export async function GET(request: NextRequest) {
  try {
    const requested = new URL(request.url).searchParams.get("status") ?? "active";
    const status = GuidanceStatusSchema.safeParse(requested);
    if (!status.success) {
      return NextResponse.json({ error: "Invalid guidance status" }, { status: 400 });
    }

    const data = await fastapiFetch(`/api/v1/guidance/org?status=${status.data}`);
    return NextResponse.json(GuidanceRuleListResponseSchema.parse(data));
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/org GET",
      error,
      "Failed to load organization guidance",
    );
  }
}

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "A JSON body is required" }, { status: 400 });
  }

  // Validated here rather than inside the upstream `try`: a malformed body is
  // the caller's fault (400) and must not be reported with the same status as
  // a backend that refused or was unreachable.
  const parsed = GuidanceRuleCreateRequestSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: parsed.error.issues[0]?.message ?? "Invalid guidance rule" },
      { status: 400 },
    );
  }

  try {
    const data = await fastapiFetch("/api/v1/guidance/org", {
      method: "POST",
      body: parsed.data,
    });
    return NextResponse.json(GuidanceRuleSchema.parse(data), { status: 201 });
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/org POST",
      error,
      "Failed to add the organization rule",
    );
  }
}
