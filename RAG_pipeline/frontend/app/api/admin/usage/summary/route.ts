import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const days = searchParams.get("days") || "30";
    const data = await fastapiFetch(`/api/v1/admin/usage/summary?days=${days}`);
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch usage summary";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
