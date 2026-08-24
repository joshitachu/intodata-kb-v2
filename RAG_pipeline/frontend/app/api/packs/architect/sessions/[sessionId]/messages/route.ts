import { NextRequest } from "next/server";
import { forwardBody, proxyPacks } from "../../../../_proxy";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ sessionId: string }> };

export async function POST(request: NextRequest, { params }: Params) {
  const { sessionId } = await params;
  return proxyPacks(request, `/architect/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    body: await forwardBody(request),
  });
}
