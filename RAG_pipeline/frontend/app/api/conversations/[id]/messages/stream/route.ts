import { NextRequest } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";
import { upstreamResponseError } from "@/lib/backendProxy";

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
    // Status and structured detail forwarded intact: a 402 licence refusal must
    // reach the browser as a refusal, not as a broken turn.
    return upstreamResponseError("conversations/[id]/messages/stream POST", res, "Failed to send message");
  }

  return new Response(res.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
