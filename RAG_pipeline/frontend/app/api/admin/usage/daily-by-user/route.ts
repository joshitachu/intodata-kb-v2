import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const days = searchParams.get("days") || "30";
    const top = searchParams.get("top") || "10";
    const data = await fastapiFetch(
      `/api/v1/admin/usage/daily-by-user?days=${days}&top=${top}`
    );
    return NextResponse.json(data);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch daily user usage";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
