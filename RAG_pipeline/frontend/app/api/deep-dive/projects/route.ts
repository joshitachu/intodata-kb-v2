import { NextRequest, NextResponse } from "next/server";
import { DeepDiveProjectsResponseSchema } from "@/types";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8001";

export async function GET(request: NextRequest) {
  try {
    const cookieHeader = request.headers.get("cookie") || "";

    const res = await fetch(`${KB_API_URL}/mcp/deep-dive/projects`, {
      headers: { Cookie: cookieHeader },
    });

    if (!res.ok) {
      const text = await res.text();
      let message = "Failed to fetch deep dive projects";
      try {
        message = JSON.parse(text).detail || message;
      } catch {
        // use default
      }
      return NextResponse.json({ error: message }, { status: res.status });
    }

    const data = await res.json();
    const validated = DeepDiveProjectsResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch projects";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
