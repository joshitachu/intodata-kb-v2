import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import {
  ConversationListResponseSchema,
  ConversationResponseSchema,
} from "@/types";

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
    const message =
      error instanceof Error ? error.message : "Failed to fetch conversations";
    return NextResponse.json({ error: message }, { status: 500 });
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
    const message =
      error instanceof Error ? error.message : "Failed to create conversation";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
