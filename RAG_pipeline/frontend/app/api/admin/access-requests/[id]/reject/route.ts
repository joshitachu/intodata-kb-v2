import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const body = await request.json().catch(() => ({}));
    const data = await fastapiFetch(`/api/v1/admin/access-requests/${id}/reject`, {
      method: "POST",
      body,
    });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/access-requests/[id]/reject POST", error, "Failed to reject request");
  }
}
