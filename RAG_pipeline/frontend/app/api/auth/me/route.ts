import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/me`, {
      headers: backendProxyHeaders(request),
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: "Not authenticated" },
        { status: res.status },
      );
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { error: "Failed to fetch user" },
      { status: 500 },
    );
  }
}
