import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function GET(request: NextRequest) {
  const cookieHeader = request.headers.get("cookie") || "";
  const { searchParams } = new URL(request.url);
  const qs = searchParams.toString();

  const res = await fetch(
    `${FASTAPI_URL}/api/v1/notifications${qs ? `?${qs}` : ""}`,
    {
      headers: { Cookie: cookieHeader },
      cache: "no-store",
    },
  );

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
