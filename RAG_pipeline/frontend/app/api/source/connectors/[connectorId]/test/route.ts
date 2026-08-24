import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJson } from "../../../_proxy";

export const dynamic = "force-dynamic";

interface Params {
  params: Promise<{ connectorId: string }>;
}

function safeConnectorId(value: string): string | null {
  const trimmed = value.trim();
  return /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(trimmed) ? trimmed : null;
}

export async function POST(request: NextRequest, { params }: Params) {
  const connectorId = safeConnectorId(decodeURIComponent((await params).connectorId));
  if (!connectorId) {
    return NextResponse.json({ error: "Invalid connector id" }, { status: 400 });
  }

  return proxyMcpJson(request, `/mcp/connectors/${connectorId}/test`, {
    method: "POST",
  });
}
