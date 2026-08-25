import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

/** Live state of an in-flight sync. Polled while the sources page is open. */
export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await fetch(`${FASTAPI_URL}/api/v1/runtime/sources/${encodeURIComponent(id)}/sync`, {
    headers: backendProxyHeaders(request),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await fetch(`${FASTAPI_URL}/api/v1/runtime/sources/${encodeURIComponent(id)}/sync`, {
    method: "POST",
    headers: backendProxyHeaders(request),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
