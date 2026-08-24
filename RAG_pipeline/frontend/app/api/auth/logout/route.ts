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
    const res = await fetchBackend(`${FASTAPI_URL}/api/v1/auth/logout`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
    });

    // Forward Set-Cookie (clear) headers from FastAPI
    return forwardSetCookies(res, NextResponse.json({ message: "Logged out" }));
  } catch (error) {
    return upstreamErrorResponse("auth/logout", error);
  }
}
