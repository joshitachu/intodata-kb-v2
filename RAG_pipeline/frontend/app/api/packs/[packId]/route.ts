import { NextRequest } from "next/server";
import { forwardBody, proxyPacks } from "../_proxy";

export const dynamic = "force-dynamic";

/** Update a shared pack (pull) or its auto-update flag. */
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ packId: string }> },
) {
  const { packId } = await params;
  return proxyPacks(request, `/${encodeURIComponent(packId)}`, {
    method: "PATCH",
    body: await forwardBody(request),
  });
}

/** Delete one of the caller's own packs. */
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ packId: string }> },
) {
  const { packId } = await params;
  return proxyPacks(request, `/${encodeURIComponent(packId)}`, { method: "DELETE" });
}
