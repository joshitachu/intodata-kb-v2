import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import {
  ConversationListResponseSchema,
  ConversationResponseSchema,
} from "@/types";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const skip = searchParams.get("skip") || "0";
    const limit = searchParams.get("limit") || "50";

    const data = await fastapiFetch(
      `/api/v1/conversations?skip=${skip}&limit=${limit}`
    );

    const validated = ConversationListResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    return upstreamErrorResponse("conversations GET", error, "Failed to fetch conversations");
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));

    const data = await fastapiFetch("/api/v1/conversations", {
      method: "POST",
      body,
    });

    const validated = ConversationResponseSchema.parse(data);
    return NextResponse.json(validated, { status: 201 });
  } catch (error) {
    return upstreamErrorResponse("conversations POST", error, "Failed to create conversation");
  }
}
