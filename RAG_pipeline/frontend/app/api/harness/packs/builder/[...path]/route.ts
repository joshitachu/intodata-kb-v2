import { NextRequest, NextResponse } from "next/server";
import { FASTAPI_URL } from "@/lib/fastapi";
import { backendProxyHeaders } from "@/lib/proxyHeaders";

async function proxy(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs/builder/${path.map(encodeURIComponent).join("/")}`, {
    method: request.method,
    headers: backendProxyHeaders(request, { json: !["GET", "HEAD"].includes(request.method) }),
    body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.text(),
  });
  return new NextResponse(await upstream.text(), { status: upstream.status, headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" } });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
