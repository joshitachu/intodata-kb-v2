import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { QueryRequestSchema, QueryResponseSchema } from "@/types";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const parsed = QueryRequestSchema.parse(body);

    const data = await fastapiFetch("/api/v1/query", {
      method: "POST",
      body: parsed,
    });

    const validated = QueryResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Query failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
