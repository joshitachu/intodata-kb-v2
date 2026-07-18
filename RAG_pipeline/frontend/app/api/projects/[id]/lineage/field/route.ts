import { NextRequest, NextResponse } from "next/server";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8002";

// Proxies GET /mcp/projects/{id}/lineage/field - the S4 field-lineage query
// API (see knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py
// ::get_field_lineage). RBAC is enforced entirely server-side via
// require_project_access on the FastAPI route (cookie-forwarded session,
// same pattern as every other project proxy route in this directory) -
// this handler never trusts or re-derives scope from the request itself,
// it only forwards the caller's own session cookie and the query params the
// UI already knows it wants (job/table/column come from a graph the user is
// already authorized to see).
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const cookieHeader = request.headers.get("cookie") || "";
    const search = request.nextUrl.searchParams;

    const upstream = new URLSearchParams();
    for (const key of ["job", "table", "column", "component", "direction", "max_depth"]) {
      const value = search.get(key);
      if (value !== null) upstream.set(key, value);
    }

    const res = await fetch(
      `${KB_API_URL}/mcp/projects/${encodeURIComponent(id)}/lineage/field?${upstream.toString()}`,
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
      // FastAPI wraps HTTPException(detail=...) as { "detail": ... }.
      // LineageDataUnavailableError (no scan/lineage.json for this project,
      // e.g. never rescanned since S4 landed) maps to a plain 404 string
      // detail - never a fabricated empty-but-200 response, so the UI can
      // tell "no lineage data at all" apart from "field has no edges".
      const detail =
        body !== null && typeof body === "object" && "detail" in body
          ? (body as { detail: unknown }).detail
          : null;
      const message = typeof detail === "string" ? detail : "Failed to fetch field lineage";
      return NextResponse.json({ error: message }, { status: res.status });
    }

    return NextResponse.json(body);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to fetch field lineage";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
