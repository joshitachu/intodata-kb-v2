import { NextResponse } from "next/server";
import { fetchBackend, upstreamErrorResponse } from "@/lib/backendProxy";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET() {
  try {
    const res = await fetchBackend(`${FASTAPI_URL}/api/v1/auth/bootstrap-status`, {
      method: "GET",
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json({ needs_bootstrap: false }, { status: res.status });
    }
    return NextResponse.json(await res.json());
  } catch (error) {
    return upstreamErrorResponse("auth/bootstrap-status", error);
  }
}
