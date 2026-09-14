import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { id } = await params;
  try {
    const body = await request.json();
    const data = await fastapiFetch(`/api/v1/admin/users/${id}/reset-password`, {
      method: "POST",
      body,
    });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/users/[id]/reset-password POST", error, "Failed to reset password");
  }
}
