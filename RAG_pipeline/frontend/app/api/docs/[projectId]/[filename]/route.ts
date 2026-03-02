import { NextRequest, NextResponse } from "next/server";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8001";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ projectId: string; filename: string }> },
) {
  const { projectId, filename } = await params;

  // Validate extension
  if (!filename.endsWith(".md") && !filename.endsWith(".pdf")) {
    return NextResponse.json(
      { error: "Only .md and .pdf files are supported" },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(
      `${KB_API_URL}/mcp/projects/${projectId}/docs/${filename}`,
    );

    if (!res.ok) {
      const text = await res.text();
      let message = "File not found";
      try {
        message = JSON.parse(text).detail || message;
      } catch {
        // use default
      }
      return NextResponse.json({ error: message }, { status: res.status });
    }

    // For markdown files, return as text
    if (filename.endsWith(".md")) {
      const text = await res.text();
      return new NextResponse(text, {
        headers: { "Content-Type": "text/markdown; charset=utf-8" },
      });
    }

    // For PDF files, stream the binary
    const blob = await res.blob();
    return new NextResponse(blob, {
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": `attachment; filename="${filename}"`,
      },
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch file";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
