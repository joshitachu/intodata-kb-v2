import { NextRequest } from "next/server";
import { forwardBody, proxyPacks } from "./_proxy";

export const dynamic = "force-dynamic";

/** Packs visible to the caller: shared ones plus their own. */
export async function GET(request: NextRequest) {
  return proxyPacks(request, "");
}

/** Register a branch of the shared pack repository. */
export async function POST(request: NextRequest) {
  return proxyPacks(request, "", { method: "POST", body: await forwardBody(request) });
}
