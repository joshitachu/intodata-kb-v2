import { NextRequest, NextResponse } from "next/server";
import { upstreamErrorResponse } from "@/lib/backendProxy";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8002";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const cookieHeader = request.headers.get("cookie") || "";
    const projectId = request.nextUrl.searchParams.get("project_id");

    if (!projectId) {
      return NextResponse.json({ error: "project_id is required" }, { status: 400 });
    }

    const upstream = await fetch(
      `${KB_API_URL}/logs/stream?project_id=${encodeURIComponent(projectId)}`,
      {
        headers: { Cookie: cookieHeader },
      },
    );

    if (!upstream.ok) {
      const text = await upstream.text();
      return new Response(text, {
        status: upstream.status,
        headers: {
          "Content-Type": upstream.headers.get("Content-Type") || "application/json",
        },
      });
    }

    return new Response(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  } catch (error) {
    return upstreamErrorResponse("source/logs GET", error, "Log stream failed");
  }
}
