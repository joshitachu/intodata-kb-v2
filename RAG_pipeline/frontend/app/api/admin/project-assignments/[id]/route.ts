import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function DELETE(_request: NextRequest, { params }: RouteParams) {
  const { id } = await params;
  try {
    const data = await fastapiFetch(`/api/v1/admin/project-assignments/${id}`, {
      method: "DELETE",
    });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/project-assignments/[id] DELETE", error, "Failed to unassign project");
  }
}
