import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function PUT(request: NextRequest) {
  const cookieHeader = request.headers.get("cookie") || "";
  const res = await fetch(`${FASTAPI_URL}/api/v1/notifications/read-all`, {
    method: "PUT",
    headers: { Cookie: cookieHeader },
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
