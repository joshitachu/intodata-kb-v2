import { NextRequest } from "next/server";
import { proxyHarnessPack } from "@/lib/harnessPacksProxy";

export async function GET(request: NextRequest, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  return proxyHarnessPack(request, `/projects/${encodeURIComponent(projectId)}/styles`);
}

export async function POST(request: NextRequest, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  return proxyHarnessPack(request, `/projects/${encodeURIComponent(projectId)}/styles`, "POST");
}
