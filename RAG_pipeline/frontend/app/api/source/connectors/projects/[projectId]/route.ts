import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJson, safeProjectId } from "../../../_proxy";

export const dynamic = "force-dynamic";

interface Params {
  params: Promise<{ projectId: string }>;
}

async function jsonBody(response: Response): Promise<Record<string, unknown>> {
  try {
    return JSON.parse(await response.text()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export async function GET(request: NextRequest, { params }: Params) {
  const raw = (await params).projectId;
  const projectId = safeProjectId(decodeURIComponent(raw));
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  const [availableResponse, bindingsResponse] = await Promise.all([
    proxyMcpJson(request, `/mcp/projects/${projectId}/available-connectors`),
    proxyMcpJson(request, `/mcp/projects/${projectId}/connector-bindings`),
  ]);
  const [available, bindings] = await Promise.all([
    jsonBody(availableResponse),
    jsonBody(bindingsResponse),
  ]);

  // Reading bindings needs project access; listing available connectors needs
  // project management. A project viewer therefore gets 403 on the second call
  // while the first succeeds -- degrade to a read-only payload instead of
  // failing the whole panel.
  if (!bindingsResponse.ok) {
    return NextResponse.json(bindings, { status: bindingsResponse.status });
  }
  if (!availableResponse.ok && availableResponse.status !== 403) {
    return NextResponse.json(available, { status: availableResponse.status });
  }

  return NextResponse.json({
    project_id: projectId,
    can_manage: availableResponse.ok,
    connectors: Array.isArray(available.connectors) ? available.connectors : [],
    bindings: Array.isArray(bindings.bindings) ? bindings.bindings : [],
  });
}
