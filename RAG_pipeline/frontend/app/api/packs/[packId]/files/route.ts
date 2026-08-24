import { NextRequest } from "next/server";
import { proxyPacks } from "../../_proxy";

export const dynamic = "force-dynamic";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ packId: string }> },
) {
  const { packId } = await params;
  return proxyPacks(request, `/${encodeURIComponent(packId)}/files`);
}
