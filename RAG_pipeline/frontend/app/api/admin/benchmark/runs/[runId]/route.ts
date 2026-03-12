import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  try {
    const data = await fastapiFetch(`/api/v1/benchmark/runs/${runId}`);
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch benchmark run";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
