import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  const body = await request.text();

  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/feedback`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
      body,
    });

    return new NextResponse(null, { status: res.status });
  } catch {
    // Feedback must never block chat UX — swallow upstream failures
    return new NextResponse(null, { status: 204 });
  }
}
