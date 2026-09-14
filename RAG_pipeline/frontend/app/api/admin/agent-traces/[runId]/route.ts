import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

interface RouteParams { params: Promise<{ runId: string }> }

export async function GET(_request: NextRequest, { params }: RouteParams) {
  const { runId } = await params;
  try {
    const data = await fastapiFetch(`/api/v1/admin/agent-traces/${encodeURIComponent(runId)}`);
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/agent-traces/[runId] GET", error, "Failed to fetch agent trace");
  }
}
