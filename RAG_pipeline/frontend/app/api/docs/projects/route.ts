import { NextResponse } from "next/server";
import { DocsProjectsResponseSchema } from "@/types";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8001";

export async function GET() {
  try {
    const res = await fetch(`${KB_API_URL}/mcp/projects`);

    if (!res.ok) {
      const text = await res.text();
      let message = "Failed to fetch projects";
      try {
        message = JSON.parse(text).detail || message;
      } catch {
        // use default
      }
      return NextResponse.json({ error: message }, { status: res.status });
    }

    const data = await res.json();

    // Filter to projects with docs and map to DocsProject shape
    const docsProjects = data.projects
      .filter((p: { doc_count: number }) => p.doc_count > 0)
      .map((p: { id: string; display_name: string; platform: string; doc_count: number }) => ({
        id: p.id,
        display_name: p.display_name,
        platform: p.platform,
        doc_count: p.doc_count,
      }));

    const result = { projects: docsProjects, total: docsProjects.length };
    const validated = DocsProjectsResponseSchema.parse(result);
    return NextResponse.json(validated);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch projects";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
