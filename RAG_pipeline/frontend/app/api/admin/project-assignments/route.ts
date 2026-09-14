import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const params = new URLSearchParams();
    const userId = searchParams.get("user_id");
    const projectId = searchParams.get("project_id");
    if (userId) params.set("user_id", userId);
    if (projectId) params.set("project_id", projectId);

    const queryString = params.toString();
    const path = `/api/v1/admin/project-assignments${queryString ? `?${queryString}` : ""}`;
    const data = await fastapiFetch(path);
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/project-assignments GET", error, "Failed to fetch assignments");
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const data = await fastapiFetch("/api/v1/admin/project-assignments", {
      method: "POST",
      body,
    });
    return NextResponse.json(data, { status: 201 });
  } catch (error) {
    return upstreamErrorResponse("admin/project-assignments POST", error, "Failed to assign project");
  }
}
