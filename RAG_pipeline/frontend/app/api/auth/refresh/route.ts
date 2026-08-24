import { NextRequest, NextResponse } from "next/server";
import {
  fetchBackend,
  forwardSetCookies,
  upstreamErrorResponse,
} from "@/lib/backendProxy";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function POST(request: NextRequest) {
  try {
    const res = await fetchBackend(`${FASTAPI_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
    });

    if (!res.ok) {
      // Status passes through unchanged: only 401 means the session is gone.
      // A 429 (shared rate-limit bucket) or 5xx must stay distinguishable, and
      // the backend's cookie-clearing headers on a real 401 must reach the
      // browser or stale cookies keep the middleware admitting the request.
      return forwardSetCookies(
        res,
        NextResponse.json(
          { error: "Token refresh failed", retryable: res.status !== 401 },
          { status: res.status },
        ),
      );
    }

    return forwardSetCookies(res, NextResponse.json(await res.json()));
  } catch (error) {
    return upstreamErrorResponse("auth/refresh", error);
  }
}
