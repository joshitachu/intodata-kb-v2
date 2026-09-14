import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import {
  ConversationDetailResponseSchema,
  ConversationResponseSchema,
} from "@/types";
import { upstreamErrorResponse } from "@/lib/backendProxy";

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
    return upstreamErrorResponse("conversations/[id] GET", error, "Failed to fetch conversation");
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
    return upstreamErrorResponse("conversations/[id] PATCH", error, "Failed to update conversation");
  }
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    await fastapiFetch(`/api/v1/conversations/${id}`, { method: "DELETE" });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    return upstreamErrorResponse("conversations/[id] DELETE", error, "Failed to delete conversation");
  }
}
