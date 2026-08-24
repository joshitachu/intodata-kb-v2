import { NextRequest } from "next/server";
import { forwardBody, proxyPacks } from "../../../_proxy";

export const dynamic = "force-dynamic";

/**
 * One pack file. The path segments are re-encoded rather than joined raw:
 * traversal is also rejected server-side, but a proxy that forwards `..`
 * verbatim is a proxy waiting to be the weak link.
 */
function filePath(packId: string, segments: string[]): string {
  const encoded = segments.map(encodeURIComponent).join("/");
  return `/${encodeURIComponent(packId)}/files/${encoded}`;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ packId: string; path: string[] }> },
) {
  const { packId, path } = await params;
  return proxyPacks(request, filePath(packId, path));
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ packId: string; path: string[] }> },
) {
  const { packId, path } = await params;
  return proxyPacks(request, filePath(packId, path), {
    method: "PUT",
    body: await forwardBody(request),
  });
}
