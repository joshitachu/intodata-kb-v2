import { NextResponse, type NextRequest } from "next/server";
import { DeepDiveProjectsResponseSchema } from "@/types";
import { fetchBackend, upstreamErrorResponse } from "@/lib/backendProxy";
import { BackendResponseError } from "@/lib/upstreamErrors";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8002";

export async function GET(request: NextRequest) {
  const path = "/mcp/deep-dive/projects";
  try {
    const res = await fetchBackend(`${KB_API_URL}${path}`, {
      headers: { Cookie: request.headers.get("cookie") || "" },
      cache: "no-store",
    });

    if (!res.ok) {
      throw new BackendResponseError(res.status, await res.text(), path);
    }

    const validated = DeepDiveProjectsResponseSchema.parse(await res.json());
    return NextResponse.json(validated);
  } catch (error) {
    // The upstream status survives (401 stays 401 so the browser rotates its
    // token) and a scanner that is simply down becomes 503, not 500 — the
    // topology picker can then say "unavailable" instead of implying the
    // account has nothing scanned.
    return upstreamErrorResponse("deep-dive/projects GET", error, "Failed to fetch projects");
  }
}
