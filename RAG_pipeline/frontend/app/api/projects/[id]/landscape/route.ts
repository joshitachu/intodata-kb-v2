import { NextRequest, NextResponse } from "next/server";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8002";

/** Cookie-forwarding BFF proxy for the scanner-produced topology artifact. */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const response = await fetch(
      `${KB_API_URL}/mcp/projects/${encodeURIComponent(id)}/landscape`,
      { headers: { Cookie: request.headers.get("cookie") || "" } },
    );
    if (!response.ok) {
      const text = await response.text();
      let message = "Failed to fetch landscape data";
      try {
        const payload = JSON.parse(text) as { detail?: unknown };
        if (typeof payload.detail === "string") message = payload.detail;
      } catch {
        // Preserve a safe generic message when the upstream response is not JSON.
      }
      return NextResponse.json({ error: message }, { status: response.status });
    }
    return NextResponse.json(await response.json());
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch landscape data";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
