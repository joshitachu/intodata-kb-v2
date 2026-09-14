import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET(request: NextRequest) {
  try {
    const status = request.nextUrl.searchParams.get("status");
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    const data = await fastapiFetch(`/api/v1/admin/access-requests${qs}`);
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/access-requests GET", error, "Failed to fetch access requests");
  }
}
