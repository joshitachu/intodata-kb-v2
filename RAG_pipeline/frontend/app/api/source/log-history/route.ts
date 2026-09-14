import { NextRequest, NextResponse } from "next/server";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { KB_API_URL, safeProjectId } from "../_proxy";

export const dynamic = "force-dynamic";

interface LogFileItem {
  filename?: string;
}

export async function GET(request: NextRequest) {
  try {
    const cookieHeader = request.headers.get("cookie") || "";
    const rawProjectId = request.nextUrl.searchParams.get("project_id") || "";
    const projectId = safeProjectId(rawProjectId);

    if (!projectId) {
      return NextResponse.json({ error: "valid project_id is required" }, { status: 400 });
    }

    const list = await fetch(`${KB_API_URL}/mcp/logs/${encodeURIComponent(projectId)}`, {
      headers: { Cookie: cookieHeader },
    });

    if (!list.ok) {
      const text = await list.text();
      return new Response(text, {
        status: list.status,
        headers: {
          "Content-Type": list.headers.get("Content-Type") || "application/json",
        },
      });
    }

    const data = await list.json();
    const files = Array.isArray(data.files) ? (data.files as LogFileItem[]) : [];
    const filename = files.find((file) => typeof file.filename === "string")?.filename ?? null;

    if (!filename) {
      return NextResponse.json({ project_id: projectId, filename: null, lines: [] });
    }

    const content = await fetch(
      `${KB_API_URL}/mcp/logs/${encodeURIComponent(projectId)}/${encodeURIComponent(filename)}`,
      { headers: { Cookie: cookieHeader } },
    );

    if (!content.ok) {
      const text = await content.text();
      return new Response(text, {
        status: content.status,
        headers: {
          "Content-Type": content.headers.get("Content-Type") || "application/json",
        },
      });
    }

    const payload = await content.json();
    const rawContent = typeof payload.content === "string" ? payload.content : "";
    const lines = rawContent.split(/\r?\n/).filter(Boolean).slice(-200);

    return NextResponse.json({ project_id: projectId, filename, lines });
  } catch (error) {
    return upstreamErrorResponse("source/log-history GET", error, "Failed to fetch log history");
  }
}
