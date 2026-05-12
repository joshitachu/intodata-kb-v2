import { NextRequest, NextResponse } from "next/server";
import { SourceProjectsResponseSchema } from "@/types";
import { KB_API_URL, stripSourceSecrets } from "../_proxy";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const cookieHeader = request.headers.get("cookie") || "";
    const upstream = await fetch(`${KB_API_URL}/mcp/projects`, {
      headers: { Cookie: cookieHeader },
    });

    if (!upstream.ok) {
      const text = await upstream.text();
      let message = "Failed to fetch sources";
      try {
        message = JSON.parse(text).detail || message;
      } catch {
        // Use default message.
      }
      return NextResponse.json({ error: message }, { status: upstream.status });
    }

    const data = stripSourceSecrets(await upstream.json());
    const validated = SourceProjectsResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch sources";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
