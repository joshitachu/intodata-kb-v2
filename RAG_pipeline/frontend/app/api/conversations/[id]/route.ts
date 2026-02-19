import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import {
  ConversationDetailResponseSchema,
  ConversationResponseSchema,
} from "@/types";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const data = await fastapiFetch(`/api/v1/conversations/${id}`);

    const validated = ConversationDetailResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch conversation";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function PATCH(request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const body = await request.json();

    const data = await fastapiFetch(`/api/v1/conversations/${id}`, {
      method: "PATCH",
      body,
    });

    const validated = ConversationResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to update conversation";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    await fastapiFetch(`/api/v1/conversations/${id}`, { method: "DELETE" });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to delete conversation";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
