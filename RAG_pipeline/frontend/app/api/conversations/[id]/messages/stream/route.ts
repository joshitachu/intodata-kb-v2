import { NextRequest } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { id } = await params;
  const body = await request.json();

  const res = await fetch(
    `${FASTAPI_URL}/api/v1/conversations/${id}/messages/stream`,
    {
      method: "POST",
      headers: backendProxyHeaders(request, { json: true }),
      body: JSON.stringify(body),
    },
  );

  if (!res.ok) {
    const detail = await res.text();
    return new Response(JSON.stringify({ error: detail }), {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(res.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
