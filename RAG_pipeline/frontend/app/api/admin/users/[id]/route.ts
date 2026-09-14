import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function PUT(request: NextRequest, { params }: RouteParams) {
  const { id } = await params;
  try {
    const body = await request.json();
    const data = await fastapiFetch(`/api/v1/admin/users/${id}`, {
      method: "PUT",
      body,
    });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/users/[id] PUT", error, "Failed to update user");
  }
}

export async function DELETE(_request: NextRequest, { params }: RouteParams) {
  const { id } = await params;
  try {
    const data = await fastapiFetch(`/api/v1/admin/users/${id}`, {
      method: "DELETE",
    });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/users/[id] DELETE", error, "Failed to deactivate user");
  }
}
