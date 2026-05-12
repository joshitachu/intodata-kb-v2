import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  const body = await request.text();

  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/telemetry/wizard-event`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
      body,
    });

    // Backend returns 204 on success — preserve it
    return new NextResponse(null, { status: res.status });
  } catch {
    // Telemetry must never block UX — swallow upstream failures
    return new NextResponse(null, { status: 204 });
  }
}
