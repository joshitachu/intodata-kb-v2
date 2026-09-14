import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { MessageResponseSchema } from "@/types";
import { upstreamErrorResponse } from "@/lib/backendProxy";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const body = await request.json();

    const data = await fastapiFetch(`/api/v1/conversations/${id}/messages/save`, {
      method: "POST",
      body,
    });

    const validated = MessageResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    return upstreamErrorResponse("conversations/[id]/messages/save POST", error, "Failed to save exchange");
  }
}
