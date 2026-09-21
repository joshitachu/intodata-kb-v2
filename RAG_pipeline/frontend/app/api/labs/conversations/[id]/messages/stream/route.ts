import { NextRequest } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

interface RouteParams {
  params: Promise<{ id: string }>;
}

/**
 * Labs lane mirror of `app/api/conversations/[id]/messages/stream/route.ts`.
 *
 * Kept byte-for-byte identical to the production route apart from the upstream
 * path. In particular it uses bare `fetch` rather than `fetchBackend`: the
 * shared helper applies a 20s abort timeout, which would cut a long agent turn
 * mid-stream. The response body is piped through untouched so the SSE frames
 * the browser parses are exactly the frames the backend emitted.
 */
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { id } = await params;
  const body = await request.json();

  const res = await fetch(
    `${FASTAPI_URL}/api/v1/labs/conversations/${id}/messages/stream`,
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
