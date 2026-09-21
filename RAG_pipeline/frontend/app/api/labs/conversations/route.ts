import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import {
  ConversationListResponseSchema,
  ConversationResponseSchema,
} from "@/types";
import { upstreamErrorResponse } from "@/lib/backendProxy";

/**
 * Labs lane mirror of `app/api/conversations/route.ts`.
 *
 * Deliberately validates with the *same* schemas as the production route:
 * these schemas strip undeclared keys, so a labs-only copy would silently drop
 * any field the chat components read and make a frontend bug look like a
 * backend one. Same schema in, same object shape out -- which is also the only
 * way the side-by-side comparison stays honest.
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const skip = searchParams.get("skip") || "0";
    const limit = searchParams.get("limit") || "50";

    const data = await fastapiFetch(
      `/api/v1/labs/conversations?skip=${skip}&limit=${limit}`
    );

    const validated = ConversationListResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    return upstreamErrorResponse(
      "labs/conversations GET",
      error,
      "Failed to fetch conversations",
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));

    const data = await fastapiFetch("/api/v1/labs/conversations", {
      method: "POST",
      body,
    });

    const validated = ConversationResponseSchema.parse(data);
    return NextResponse.json(validated, { status: 201 });
  } catch (error) {
    return upstreamErrorResponse(
      "labs/conversations POST",
      error,
      "Failed to create conversation",
    );
  }
}
