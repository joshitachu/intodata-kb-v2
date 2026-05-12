import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ step: string }> },
) {
  const { step } = await params;
  const body = await request.text();

  const res = await fetch(`${FASTAPI_URL}/api/v1/onboarding/step/${step}`, {
    method: "PUT",
    headers: backendProxyHeaders(request, { json: true }),
    body,
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
