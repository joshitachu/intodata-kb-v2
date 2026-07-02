import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET() {
  try {
    const data = await fastapiFetch("/api/v1/admin/model-config");
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to load model config";
    const status = message.includes("403") ? 403 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}

export async function PUT(request: NextRequest) {
  try {
    const body = await request.json();
    const data = await fastapiFetch("/api/v1/admin/model-config", {
      method: "PUT",
      body,
    });
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to update model config";
    const status = message.includes("403")
      ? 403
      : message.includes("400")
        ? 400
        : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
