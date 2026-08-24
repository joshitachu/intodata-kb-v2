import { NextRequest } from "next/server";
import { forwardBody, proxyPacks } from "../../_proxy";

export const dynamic = "force-dynamic";

/** Push the pack as a branch and open its pull request. */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ packId: string }> },
) {
  const { packId } = await params;
  return proxyPacks(request, `/${encodeURIComponent(packId)}/publish`, {
    method: "POST",
    body: await forwardBody(request),
  });
}
