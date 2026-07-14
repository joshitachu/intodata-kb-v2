import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";
import { HarnessResumeRequestSchema } from "@/types";

export async function POST(request: NextRequest, context: { params: Promise<{ runId: string }> }) {
  try {
    const { runId } = await context.params;
    const body = HarnessResumeRequestSchema.parse(await request.json());
    const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/workflows/${encodeURIComponent(runId)}/resume`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
      body: JSON.stringify(body),
    });
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Workflow could not be resumed" }, { status: upstream.status });
  } catch {
    return NextResponse.json({ error: "Invalid workflow response" }, { status: 400 });
  }
}
