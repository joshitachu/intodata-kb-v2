import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ step: string }> },
) {
  const { step } = await params;
  const body = await request.text();

  const res = await fetch(`${FASTAPI_URL}/api/v1/onboarding/company/step/${step}`, {
    method: "PUT",
    headers: backendProxyHeaders(request, { json: true }),
    body,
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
