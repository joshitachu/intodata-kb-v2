import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET(request: NextRequest) {
  try {
    const query = new URL(request.url).searchParams.toString();
    const data = await fastapiFetch(
      `/api/v1/admin/agent-traces${query ? `?${query}` : ""}`,
    );
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/agent-traces GET", error, "Failed to fetch agent traces");
  }
}
