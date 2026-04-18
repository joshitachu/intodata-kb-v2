import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const cookieHeader = request.headers.get("cookie") || "";

  const res = await fetch(
    `${FASTAPI_URL}/api/v1/notifications/${id}/read`,
    {
      method: "PUT",
      headers: { Cookie: cookieHeader },
    },
  );

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
