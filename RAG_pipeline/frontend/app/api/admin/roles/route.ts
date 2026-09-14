import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET() {
  try {
    const data = await fastapiFetch("/api/v1/admin/roles");
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/roles GET", error, "Failed to fetch roles");
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const data = await fastapiFetch("/api/v1/admin/roles", {
      method: "POST",
      body,
    });
    return NextResponse.json(data, { status: 201 });
  } catch (error) {
    return upstreamErrorResponse("admin/roles POST", error, "Failed to create role");
  }
}
