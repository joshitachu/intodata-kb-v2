import { NextRequest, NextResponse } from "next/server";
import { upstreamErrorResponse } from "@/lib/backendProxy";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8002";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; jobName: string }> },
) {
  const { id, jobName } = await params;
  try {
    const cookieHeader = request.headers.get("cookie") || "";

    const res = await fetch(
      `${KB_API_URL}/mcp/projects/${encodeURIComponent(id)}/jobs/${encodeURIComponent(jobName)}/detail`,
      { headers: { Cookie: cookieHeader } },
    );

    if (!res.ok) {
      const text = await res.text();
      let message = "Failed to fetch job detail";
      try {
        message = JSON.parse(text).detail || message;
      } catch {
        // use default
      }
      return NextResponse.json({ error: message }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("projects/[id]/jobs/[jobName]/detail GET", error, "Failed to fetch job detail");
  }
}
