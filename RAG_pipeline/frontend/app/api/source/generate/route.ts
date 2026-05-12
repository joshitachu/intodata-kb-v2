import { NextRequest, NextResponse } from "next/server";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8002";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const cookieHeader = request.headers.get("cookie") || "";
    const body = await request.json();

    const upstream = await fetch(`${KB_API_URL}/mcp/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: cookieHeader,
      },
      body: JSON.stringify(body),
    });

    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") || "application/json",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Generation failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
