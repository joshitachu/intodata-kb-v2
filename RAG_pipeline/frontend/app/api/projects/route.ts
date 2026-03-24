import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { UserProjectsResponseSchema } from "@/types";

export async function GET() {
  try {
    const data = await fastapiFetch("/api/v1/projects");
    const validated = UserProjectsResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to load projects";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
