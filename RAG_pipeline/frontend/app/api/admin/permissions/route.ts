import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET() {
  try {
    const data = await fastapiFetch("/api/v1/admin/permissions");
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/permissions GET", error, "Failed to fetch permissions");
  }
}
