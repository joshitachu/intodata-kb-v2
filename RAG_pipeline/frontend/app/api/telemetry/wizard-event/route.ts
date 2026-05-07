import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  const cookieHeader = request.headers.get("cookie") || "";
  const body = await request.text();

  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/telemetry/wizard-event`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: cookieHeader,
      },
      body,
    });

    // Backend returns 204 on success — preserve it
    return new NextResponse(null, { status: res.status });
  } catch {
    // Telemetry must never block UX — swallow upstream failures
    return new NextResponse(null, { status: 204 });
  }
}
