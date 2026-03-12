import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

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
    const message = error instanceof Error ? error.message : "Failed to unassign project";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
