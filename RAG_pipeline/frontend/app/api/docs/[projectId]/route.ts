import { NextRequest, NextResponse } from "next/server";
import { DocListResponseSchema } from "@/types";
import { upstreamErrorResponse } from "@/lib/backendProxy";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8001";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const { projectId } = await params;
  const cookieHeader = request.headers.get("cookie") || "";

  try {
    const res = await fetch(
      `${KB_API_URL}/mcp/projects/${projectId}/docs`,
      { headers: { Cookie: cookieHeader } },
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
    return upstreamErrorResponse("docs/[projectId] GET", error, "Failed to fetch docs");
  }
}
