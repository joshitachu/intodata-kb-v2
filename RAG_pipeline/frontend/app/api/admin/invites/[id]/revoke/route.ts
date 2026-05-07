import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function POST(_: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const data = await fastapiFetch(`/api/v1/admin/invites/${id}/revoke`, { method: "POST" });
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to revoke invite";
    const status = message.includes("409") ? 409 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
