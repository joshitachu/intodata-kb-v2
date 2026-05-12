import { NextRequest, NextResponse } from "next/server";
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
    const message = error instanceof Error ? error.message : "Push to RAG failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
