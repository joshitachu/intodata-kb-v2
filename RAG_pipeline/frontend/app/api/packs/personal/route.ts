import { NextRequest } from "next/server";
import { forwardBody, proxyPacks } from "../_proxy";

export const dynamic = "force-dynamic";

/** Author a pack against the caller's own account. */
export async function POST(request: NextRequest) {
  return proxyPacks(request, "/personal", {
    method: "POST",
    body: await forwardBody(request),
  });
}
