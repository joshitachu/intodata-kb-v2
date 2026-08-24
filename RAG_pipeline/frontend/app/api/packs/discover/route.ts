import { NextRequest } from "next/server";
import { forwardBody, proxyPacks } from "../_proxy";

export const dynamic = "force-dynamic";

/** Branches of the shared repository that are valid packs. */
export async function POST(request: NextRequest) {
  return proxyPacks(request, "/discover", {
    method: "POST",
    body: await forwardBody(request),
  });
}
