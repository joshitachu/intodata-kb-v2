import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { HealthResponseSchema } from "@/types";

export async function GET() {
  try {
    const data = await fastapiFetch("/health");
    const validated = HealthResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Health check failed";
    return NextResponse.json(
      { status: "unhealthy", error: message },
      { status: 503 },
    );
  }
}
