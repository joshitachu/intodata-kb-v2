import { NextRequest } from "next/server";
import { proxyHarnessPack } from "@/lib/harnessPacksProxy";

export async function PUT(request: NextRequest, context: { params: Promise<{ packId: string }> }) {
  const { packId } = await context.params;
  return proxyHarnessPack(request, `/styles/private/${encodeURIComponent(packId)}/quality`, "PUT");
}
