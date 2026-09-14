import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function POST(_: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const data = await fastapiFetch(`/api/v1/admin/invites/${id}/resend`, { method: "POST" });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/invites/[id]/resend POST", error, "Failed to resend invite");
  }
}
