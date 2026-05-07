import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const data = await fastapiFetch("/api/v1/admin/email/test", { method: "POST", body });
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to send test email";
    const status = message.includes("502") ? 502 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
