import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { MessageResponseSchema } from "@/types";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const body = await request.json();

    const data = await fastapiFetch(`/api/v1/conversations/${id}/messages`, {
      method: "POST",
      body,
    });

    const validated = MessageResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to send message";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
