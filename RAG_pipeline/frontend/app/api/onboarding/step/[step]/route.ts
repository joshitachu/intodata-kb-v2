import { NextRequest, NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ step: string }> },
) {
  const { step } = await params;
  const cookieHeader = request.headers.get("cookie") || "";
  const body = await request.text();

  const res = await fetch(`${FASTAPI_URL}/api/v1/onboarding/step/${step}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Cookie: cookieHeader,
    },
    body,
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
