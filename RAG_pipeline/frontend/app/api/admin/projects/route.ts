import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const data = await fastapiFetch("/api/v1/admin/projects", {
      method: "POST",
      body,
    });
    return NextResponse.json(data, { status: 201 });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to create project";
    const status = message.includes("409")
      ? 409
      : message.includes("403")
        ? 403
        : message.includes("400")
          ? 400
          : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
