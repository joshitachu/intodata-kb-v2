import { NextRequest, NextResponse } from "next/server";
import { fetchBackend, upstreamErrorResponse } from "@/lib/backendProxy";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  try {
    const res = await fetchBackend(`${FASTAPI_URL}/api/v1/auth/me`, {
      headers: backendProxyHeaders(request),
    });

    if (!res.ok) {
      // Pass the backend's status through untouched. The client distinguishes
      // 401 ("logged out") from everything else ("retry"), so flattening a
      // 429/5xx into any other code here would log the user out spuriously.
      return NextResponse.json(
        { error: "Not authenticated" },
        { status: res.status },
      );
    }

    return NextResponse.json(await res.json());
  } catch (error) {
    return upstreamErrorResponse("auth/me", error);
  }
}
