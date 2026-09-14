import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET() {
  try {
    const data = await fastapiFetch("/api/v1/admin/users");
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/users GET", error, "Failed to fetch users");
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const data = await fastapiFetch("/api/v1/admin/users", {
      method: "POST",
      body,
    });
    return NextResponse.json(data, { status: 201 });
  } catch (error) {
    return upstreamErrorResponse("admin/users POST", error, "Failed to create user");
  }
}
