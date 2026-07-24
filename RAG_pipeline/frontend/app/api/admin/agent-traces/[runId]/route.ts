import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

interface RouteParams { params: Promise<{ runId: string }> }

export async function GET(_request: NextRequest, { params }: RouteParams) {
  const { runId } = await params;
  try {
    const data = await fastapiFetch(`/api/v1/admin/agent-traces/${encodeURIComponent(runId)}`);
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch agent trace";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
