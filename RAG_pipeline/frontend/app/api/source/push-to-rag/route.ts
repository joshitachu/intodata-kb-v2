import { NextRequest } from "next/server";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { proxyMcpJson } from "../_proxy";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    return proxyMcpJson(request, "/mcp/push-to-rag", {
      method: "POST",
      body: JSON.stringify(body),
    });
  } catch (error) {
    return upstreamErrorResponse("source/push-to-rag POST", error, "Push to RAG failed");
  }
}
