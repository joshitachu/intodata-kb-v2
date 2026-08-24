import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJson, safeProjectId } from "../../../../_proxy";

export const dynamic = "force-dynamic";

interface Params {
  params: Promise<{ projectId: string; connectorId: string }>;
}

function safeConnectorId(value: string): string | null {
  const trimmed = value.trim();
  return /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(trimmed) ? trimmed : null;
}

async function ids(params: Params["params"]) {
  const raw = await params;
  return {
    projectId: safeProjectId(decodeURIComponent(raw.projectId)),
    connectorId: safeConnectorId(decodeURIComponent(raw.connectorId)),
  };
}

export async function PUT(request: NextRequest, { params }: Params) {
  const { projectId, connectorId } = await ids(params);
  if (!projectId || !connectorId) {
    return NextResponse.json({ error: "Invalid project or connector id" }, { status: 400 });
  }

  const body = await request.text();
  return proxyMcpJson(
    request,
    `/mcp/projects/${projectId}/connector-bindings/${connectorId}`,
    { method: "PUT", body, headers: { "Content-Type": "application/json" } },
  );
}

export async function DELETE(request: NextRequest, { params }: Params) {
  const { projectId, connectorId } = await ids(params);
  if (!projectId || !connectorId) {
    return NextResponse.json({ error: "Invalid project or connector id" }, { status: 400 });
  }

  return proxyMcpJson(
    request,
    `/mcp/projects/${projectId}/connector-bindings/${connectorId}`,
    { method: "DELETE" },
  );
}
