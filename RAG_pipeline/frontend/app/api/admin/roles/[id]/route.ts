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
    const data = await fastapiFetch(`/api/v1/admin/roles/${id}`, {
      method: "PUT",
      body,
    });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/roles/[id] PUT", error, "Failed to update role");
  }
}

export async function DELETE(_request: NextRequest, { params }: RouteParams) {
  const { id } = await params;
  try {
    const data = await fastapiFetch(`/api/v1/admin/roles/${id}`, {
      method: "DELETE",
    });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/roles/[id] DELETE", error, "Failed to delete role");
  }
}
