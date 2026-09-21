import { NextRequest, NextResponse } from "next/server";
import { fetchBackend, upstreamErrorResponse } from "@/lib/backendProxy";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

/**
 * Health of the experimental OpenCode lane.
 *
 * The payload is forwarded unvalidated, like `app/api/runtime/health`: a health
 * probe's whole job is to describe a lane whose contract is still moving, and a
 * schema here would strip fields the banner needs (or reject the response
 * outright) the first time the backend adds one. Transport failures are
 * classified by `upstreamErrorResponse`, so "the lane is down" is a 503 and not
 * an unhandled throw.
 */
export async function GET(request: NextRequest) {
  try {
    const res = await fetchBackend(
      `${FASTAPI_URL}/api/v1/labs/health${request.nextUrl.search}`,
      { headers: backendProxyHeaders(request), cache: "no-store" },
    );

    const data: unknown = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    return upstreamErrorResponse(
      "labs/health GET",
      error,
      "Labs lane unavailable",
    );
  }
}
