import { NextRequest, NextResponse } from "next/server";
import { DocListResponseSchema } from "@/types";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8001";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const { projectId } = await params;

  try {
    const res = await fetch(
      `${KB_API_URL}/mcp/projects/${projectId}/docs`,
    );

    if (!res.ok) {
      const text = await res.text();
      let message = "Failed to fetch docs";
      try {
        message = JSON.parse(text).detail || message;
      } catch {
        // use default
      }
      return NextResponse.json({ error: message }, { status: res.status });
    }

    const data = await res.json();
    const validated = DocListResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch docs";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
