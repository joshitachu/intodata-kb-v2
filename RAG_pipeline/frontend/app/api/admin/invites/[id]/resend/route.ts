import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function POST(_: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const data = await fastapiFetch(`/api/v1/admin/invites/${id}/resend`, { method: "POST" });
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to resend invite";
    const status = message.includes("502") ? 502 : message.includes("409") ? 409 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
