import { NextRequest } from "next/server";
import { proxyPacks } from "../../_proxy";

export const dynamic = "force-dynamic";

/**
 * The built-in text for one prompt slot.
 *
 * Segments are re-encoded rather than joined raw, same as the pack-file route:
 * the server also rejects anything outside its slot catalog, but a proxy that
 * forwards `..` verbatim is a proxy waiting to be the weak link.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyPacks(request, `/prompt-slots/${path.map(encodeURIComponent).join("/")}`);
}
