import { NextRequest, NextResponse } from "next/server";
import { PdfGenerateResponseSchema } from "@/types";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8001";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const { projectId } = await params;
  const cookieHeader = request.headers.get("cookie") || "";

  try {
    const res = await fetch(
      `${KB_API_URL}/mcp/projects/${projectId}/generate-pdfs`,
      {
        method: "POST",
        headers: { Cookie: cookieHeader },
      },
    );

    if (!res.ok) {
      const text = await res.text();
      let message = "Failed to generate PDFs";
      try {
        message = JSON.parse(text).detail || message;
      } catch {
        // use default
      }
      return NextResponse.json({ error: message }, { status: res.status });
    }

    const data = await res.json();
    const validated = PdfGenerateResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to generate PDFs";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
