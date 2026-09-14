import { NextRequest, NextResponse } from "next/server";
import { upstreamErrorResponse } from "@/lib/backendProxy";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8001";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string; filename: string }> },
) {
  const { projectId, filename } = await params;
  const cookieHeader = request.headers.get("cookie") || "";
  // A partitioned project stores the same doc names under several
  // sub-projects, so the filename alone does not identify a file there.
  const subproject = request.nextUrl.searchParams.get("subproject");

  // Validate extension
  if (!filename.endsWith(".md") && !filename.endsWith(".pdf")) {
    return NextResponse.json(
      { error: "Only .md and .pdf files are supported" },
      { status: 400 },
    );
  }

  const query = subproject
    ? `?subproject=${encodeURIComponent(subproject)}`
    : "";

  try {
    const res = await fetch(
      `${KB_API_URL}/mcp/projects/${projectId}/docs/${encodeURIComponent(filename)}${query}`,
      { headers: { Cookie: cookieHeader } },
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
    return upstreamErrorResponse("docs/[projectId]/[filename] GET", error, "Failed to fetch file");
  }
}
