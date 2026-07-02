import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const params = new URLSearchParams();
    const skip = searchParams.get("skip");
    const limit = searchParams.get("limit");
    const userId = searchParams.get("user_id");
    if (skip) params.set("skip", skip);
    if (limit) params.set("limit", limit);
    if (userId) params.set("user_id", userId);

    const queryString = params.toString();
    const path = `/api/v1/admin/usage/recent-threads${queryString ? `?${queryString}` : ""}`;
    const data = await fastapiFetch(path);
    return NextResponse.json(data);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch recent threads";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
