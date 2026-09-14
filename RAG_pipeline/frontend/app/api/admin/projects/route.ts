import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const data = await fastapiFetch("/api/v1/admin/projects", {
      method: "POST",
      body,
    });
    return NextResponse.json(data, { status: 201 });
  } catch (error) {
    return upstreamErrorResponse("admin/projects POST", error, "Failed to create project");
  }
}

export async function GET() {
  try {
    const data = await fastapiFetch("/api/v1/admin/projects");
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/projects GET", error, "Failed to load projects");
  }
}
