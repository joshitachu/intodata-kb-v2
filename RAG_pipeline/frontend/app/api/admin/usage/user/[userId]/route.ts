import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ userId: string }> }
) {
  try {
    const { userId } = await params;
    const { searchParams } = new URL(request.url);
    const days = searchParams.get("days") || "30";
    const data = await fastapiFetch(`/api/v1/admin/usage/user/${userId}?days=${days}`);
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/usage/user/[userId] GET", error, "Failed to fetch user detail usage");
  }
}
