import { NextRequest } from "next/server";
import { forwardBody, proxyPacks } from "../../../_proxy";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ sessionId: string }> };

export async function GET(request: NextRequest, { params }: Params) {
  const { sessionId } = await params;
  return proxyPacks(request, `/architect/sessions/${encodeURIComponent(sessionId)}`);
}

export async function DELETE(request: NextRequest, { params }: Params) {
  const { sessionId } = await params;
  return proxyPacks(request, `/architect/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    body: await forwardBody(request),
  });
}
