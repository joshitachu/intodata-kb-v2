import { NextRequest } from "next/server";
import { forwardBody, proxyPacks } from "../../_proxy";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  return proxyPacks(request, "/architect/sessions", {
    method: "POST",
    body: await forwardBody(request),
  });
}
