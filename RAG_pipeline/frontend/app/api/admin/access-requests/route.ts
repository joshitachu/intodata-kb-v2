import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  try {
    const status = request.nextUrl.searchParams.get("status");
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    const data = await fastapiFetch(`/api/v1/admin/access-requests${qs}`);
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch access requests";
    const status = message.includes("403") ? 403 : message.includes("401") ? 401 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
