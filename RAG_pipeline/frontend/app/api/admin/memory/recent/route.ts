import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET(request: NextRequest) {
  try {
    const sp = request.nextUrl.searchParams;
    const qs = new URLSearchParams();
    if (sp.has("limit")) qs.set("limit", sp.get("limit")!);
    const query = qs.toString() ? `?${qs.toString()}` : "";
    const data = await fastapiFetch(
      `/api/v1/memory/recent-captures${query}`,
    );
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/memory/recent GET", error, "Failed to fetch recent captures");
  }
}
