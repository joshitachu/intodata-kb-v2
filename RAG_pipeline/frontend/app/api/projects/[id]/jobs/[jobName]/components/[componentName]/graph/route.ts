import { NextRequest, NextResponse } from "next/server";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8002";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; jobName: string; componentName: string }> },
) {
  const { id, jobName, componentName } = await params;
  try {
    const cookieHeader = request.headers.get("cookie") || "";

    const res = await fetch(
      `${KB_API_URL}/mcp/projects/${encodeURIComponent(id)}/jobs/${encodeURIComponent(jobName)}/components/${encodeURIComponent(componentName)}/graph`,
      { headers: { Cookie: cookieHeader } },
    );

    const text = await res.text();
    let body: unknown = null;
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }

    if (!res.ok) {
      // FastAPI wraps HTTPException(detail=...) as { "detail": ... } - the
      // level-3 contract's 404 fallback marker is
      // `{ detail: { fallback: "inspector" } }` on the wire. Unwrap and
      // flatten it here so useComponentGraph only has to check a top-level
      // `{ fallback: "inspector" }` shape.
      const detail =
        body !== null && typeof body === "object" && "detail" in body
          ? (body as { detail: unknown }).detail
          : null;

      if (
        res.status === 404 &&
        detail !== null &&
        typeof detail === "object" &&
        "fallback" in detail
      ) {
        return NextResponse.json(detail, { status: 404 });
      }

      const message = typeof detail === "string" ? detail : "Failed to fetch component graph";
      return NextResponse.json({ error: message }, { status: res.status });
    }

    return NextResponse.json(body);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch component graph";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
