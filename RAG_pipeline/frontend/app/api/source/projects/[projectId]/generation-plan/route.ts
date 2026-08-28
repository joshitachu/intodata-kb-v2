import { NextRequest, NextResponse } from "next/server";
import { GenerationPlanSchema } from "@/types";
import { KB_API_URL, safeProjectId } from "../../../_proxy";

export const dynamic = "force-dynamic";

interface Params {
  params: Promise<{ projectId: string }>;
}

/**
 * Read-only dry run of the next documentation run.
 *
 * Validated here rather than passed through, because the dialog renders money
 * decisions from these numbers: "1 of 6 sub-projects will regenerate" has to be
 * the server's answer or not shown at all. A shape the schema cannot parse is
 * reported as a failure so the dialog falls back to its plain form, instead of
 * rendering `undefined of undefined`.
 */
export async function GET(request: NextRequest, { params }: Params) {
  const projectId = safeProjectId(decodeURIComponent((await params).projectId));
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  try {
    const cookieHeader = request.headers.get("cookie") || "";
    const upstream = await fetch(
      `${KB_API_URL}/mcp/projects/${projectId}/generation-plan`,
      { headers: { Cookie: cookieHeader } },
    );

    if (!upstream.ok) {
      const text = await upstream.text();
      let message = "Could not read the generation plan";
      try {
        message = JSON.parse(text).detail || message;
      } catch {
        // Non-JSON upstream body; keep the default message.
      }
      return NextResponse.json({ error: message }, { status: upstream.status });
    }

    return NextResponse.json(GenerationPlanSchema.parse(await upstream.json()));
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Could not read the generation plan";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
