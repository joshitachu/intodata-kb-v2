import { NextRequest, NextResponse } from "next/server";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8001";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const cookieHeader = request.headers.get("cookie") || "";

    const res = await fetch(`${KB_API_URL}/mcp/projects/${encodeURIComponent(id)}/graph`, {
      headers: { Cookie: cookieHeader },
    });

    if (!res.ok) {
      const text = await res.text();
      let message = "Failed to fetch graph data";
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
    const message =
      error instanceof Error ? error.message : "Failed to fetch graph data";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
