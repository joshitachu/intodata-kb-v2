import { NextRequest, NextResponse } from "next/server";
import { FASTAPI_URL } from "@/lib/fastapi";
import { backendProxyHeaders } from "@/lib/proxyHeaders";

async function proxy(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const hasBody = !["GET", "HEAD"].includes(request.method);
  const contentType = request.headers.get("content-type");
  // Buffer the body before proxying. Passing NextRequest.body forwards a
  // ReadableStream, which Node fetch rejects without a non-standard duplex
  // option and caused every workbench mutation to fail before reaching FastAPI.
  const body = hasBody ? await request.arrayBuffer() : undefined;
  const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs/builder/${path.map(encodeURIComponent).join("/")}`, {
    method: request.method,
    headers: { ...backendProxyHeaders(request), ...(contentType ? { "Content-Type": contentType } : {}) },
    body,
  });
  return new NextResponse(await upstream.text(), { status: upstream.status, headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" } });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
