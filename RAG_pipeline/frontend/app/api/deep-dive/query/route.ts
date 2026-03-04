import { NextRequest } from "next/server";
import { DeepDiveRequestSchema } from "@/types";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8001";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const parsed = DeepDiveRequestSchema.parse(body);
    const cookieHeader = request.headers.get("cookie") || "";

    const upstream = await fetch(`${KB_API_URL}/mcp/deep-dive`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: cookieHeader,
      },
      body: JSON.stringify(parsed),
    });

    if (!upstream.ok) {
      const text = await upstream.text();
      let message = "Deep dive request failed";
      try {
        message = JSON.parse(text).detail || message;
      } catch {
        // use default
      }
      return new Response(JSON.stringify({ error: message }), {
        status: upstream.status,
        headers: { "Content-Type": "application/json" },
      });
    }

    // Pass SSE stream through directly
    return new Response(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Deep dive failed";
    return new Response(JSON.stringify({ error: message }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
}
