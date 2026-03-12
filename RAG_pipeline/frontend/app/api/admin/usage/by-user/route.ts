import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const days = searchParams.get("days") || "30";
    const data = await fastapiFetch(`/api/v1/admin/usage/by-user?days=${days}`);
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch user usage";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
