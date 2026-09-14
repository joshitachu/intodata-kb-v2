import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const params = new URLSearchParams();
    const skip = searchParams.get("skip");
    const limit = searchParams.get("limit");
    const iface = searchParams.get("interface");
    const userId = searchParams.get("user_id");
    if (skip) params.set("skip", skip);
    if (limit) params.set("limit", limit);
    if (iface) params.set("interface", iface);
    if (userId) params.set("user_id", userId);

    const queryString = params.toString();
    const path = `/api/v1/admin/usage/recent${queryString ? `?${queryString}` : ""}`;
    const data = await fastapiFetch(path);
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/usage/recent GET", error, "Failed to fetch recent usage");
  }
}
