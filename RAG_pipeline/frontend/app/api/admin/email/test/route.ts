import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const data = await fastapiFetch("/api/v1/admin/email/test", { method: "POST", body });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/email/test POST", error, "Failed to send test email");
  }
}
