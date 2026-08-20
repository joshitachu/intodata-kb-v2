import { NextResponse } from "next/server";
import { DocsProjectsResponseSchema } from "@/types";
import { fastapiFetch } from "@/lib/fastapi";

export async function GET() {
  try {
    const data = await fastapiFetch<{
      projects: Array<{ id: string; display_name: string; platform: string; doc_count: number }>;
    }>("/api/v1/projects");
    const result = { projects: data.projects, total: data.projects.length };
    const validated = DocsProjectsResponseSchema.parse(result);
    return NextResponse.json(validated);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch projects";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
