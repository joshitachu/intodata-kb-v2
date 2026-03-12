import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

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
    const message = error instanceof Error ? error.message : "Failed to update role";
    return NextResponse.json({ error: message }, { status: 500 });
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
    const message = error instanceof Error ? error.message : "Failed to delete role";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
