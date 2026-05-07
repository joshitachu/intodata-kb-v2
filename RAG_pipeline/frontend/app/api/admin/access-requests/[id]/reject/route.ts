import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

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
    const message = error instanceof Error ? error.message : "Failed to reject request";
    const status = message.includes("409") ? 409 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
