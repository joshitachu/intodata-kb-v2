import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET(request: NextRequest) {
  const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs/documentation-packs`, {
    headers: backendProxyHeaders(request),
  });
  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(
    upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Documentation Packs could not be loaded" },
    { status: upstream.status },
  );
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs/documentation-packs`, {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
      body: JSON.stringify(body),
    });
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(
      upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Documentation Pack could not be created" },
      { status: upstream.status },
    );
  } catch {
    return NextResponse.json({ error: "Invalid documentation Pack" }, { status: 400 });
  }
}
