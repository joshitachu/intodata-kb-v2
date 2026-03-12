import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET() {
  try {
    const data = await fastapiFetch("/api/v1/admin/permissions");
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch permissions";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
