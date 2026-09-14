import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { SlashCommandListResponseSchema } from "@/types";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET() {
  try {
    const data = await fastapiFetch("/api/v1/conversations/commands");
    return NextResponse.json(SlashCommandListResponseSchema.parse(data));
  } catch (error) {
    return upstreamErrorResponse("conversations/commands GET", error, "Command discovery is unavailable");
  }
}
