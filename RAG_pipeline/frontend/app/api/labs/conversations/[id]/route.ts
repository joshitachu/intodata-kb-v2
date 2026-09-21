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

/**
 * Labs lane mirror of `app/api/conversations/[id]/route.ts`. Same schemas as
 * production on purpose -- see the note in `../route.ts`.
 */
export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const data = await fastapiFetch(`/api/v1/labs/conversations/${id}`);

    const validated = ConversationDetailResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    return upstreamErrorResponse(
      "labs/conversations/[id] GET",
      error,
      "Failed to fetch conversation",
    );
  }
}

export async function PATCH(request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const body = await request.json();

    const data = await fastapiFetch(`/api/v1/labs/conversations/${id}`, {
      method: "PATCH",
      body,
    });

    const validated = ConversationResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    return upstreamErrorResponse(
      "labs/conversations/[id] PATCH",
      error,
      "Failed to update conversation",
    );
  }
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    await fastapiFetch(`/api/v1/labs/conversations/${id}`, { method: "DELETE" });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    return upstreamErrorResponse(
      "labs/conversations/[id] DELETE",
      error,
      "Failed to delete conversation",
    );
  }
}
