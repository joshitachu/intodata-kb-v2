import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  try {
    const query = new URL(request.url).searchParams.toString();
    const data = await fastapiFetch(
      `/api/v1/admin/agent-traces${query ? `?${query}` : ""}`,
    );
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch agent traces";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
