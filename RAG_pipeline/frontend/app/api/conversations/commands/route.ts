import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { SlashCommandListResponseSchema } from "@/types";

export async function GET() {
  try {
    const data = await fastapiFetch("/api/v1/conversations/commands");
    return NextResponse.json(SlashCommandListResponseSchema.parse(data));
  } catch (error) {
    const message = error instanceof Error ? error.message : "Command discovery is unavailable";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
