import { NextRequest } from "next/server";
import { proxyHarnessPack } from "@/lib/harnessPacksProxy";

export async function POST(request: NextRequest, context: { params: Promise<{ projectId: string; packId: string }> }) {
  const { projectId, packId } = await context.params;
  return proxyHarnessPack(request, `/projects/${encodeURIComponent(projectId)}/styles/${encodeURIComponent(packId)}/versions`, "POST");
}
