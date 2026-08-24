import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJson, safeProjectId } from "../../_proxy";

export const dynamic = "force-dynamic";

// The MCP API only exposes bindings per project, so the reverse view
// ("which projects use this connection?") is a server-side fan-out. Bounded so
// a workspace with many projects cannot open an unbounded number of sockets.
const LOOKUP_BATCH_SIZE = 8;

interface ProjectRef {
  id: string;
  display_name: string;
}

interface ConnectorUsageEntry {
  project_id: string;
  display_name: string;
  source_count: number;
  enabled: boolean;
}

async function jsonBody(response: Response): Promise<Record<string, unknown>> {
  try {
    return JSON.parse(await response.text()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function readProjects(body: Record<string, unknown>): ProjectRef[] {
  if (!Array.isArray(body.projects)) return [];
  const projects: ProjectRef[] = [];
  for (const raw of body.projects) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Record<string, unknown>;
    const id = typeof item.id === "string" ? item.id : "";
    if (!safeProjectId(id)) continue;
    projects.push({
      id,
      display_name: typeof item.display_name === "string" && item.display_name.trim() ? item.display_name : id,
    });
  }
  return projects;
}

function readBindings(body: Record<string, unknown>): Record<string, unknown>[] {
  if (!Array.isArray(body.bindings)) return [];
  return body.bindings.filter(
    (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
  );
}

export async function GET(request: NextRequest) {
  const projectsResponse = await proxyMcpJson(request, "/mcp/projects", { cache: "no-store" });
  const projectsBody = await jsonBody(projectsResponse);
  if (!projectsResponse.ok) {
    return NextResponse.json(projectsBody, { status: projectsResponse.status });
  }

  const projects = readProjects(projectsBody);
  const usage: Record<string, ConnectorUsageEntry[]> = {};

  for (let offset = 0; offset < projects.length; offset += LOOKUP_BATCH_SIZE) {
    const batch = projects.slice(offset, offset + LOOKUP_BATCH_SIZE);
    const results = await Promise.all(
      batch.map(async (project) => {
        const response = await proxyMcpJson(
          request,
          `/mcp/projects/${project.id}/connector-bindings`,
          { cache: "no-store" },
        );
        // A project the caller cannot read simply contributes nothing to the
        // rollup; it must never fail the whole view.
        if (!response.ok) return null;
        return { project, bindings: readBindings(await jsonBody(response)) };
      }),
    );

    for (const result of results) {
      if (!result) continue;
      for (const binding of result.bindings) {
        const connectorId = binding.connector_instance_id;
        if (typeof connectorId !== "string" || !connectorId) continue;
        const entries = usage[connectorId] ?? (usage[connectorId] = []);
        entries.push({
          project_id: result.project.id,
          display_name: result.project.display_name,
          source_count: Array.isArray(binding.source_ids) ? binding.source_ids.length : 0,
          enabled: binding.enabled === true,
        });
      }
    }
  }

  return NextResponse.json({ usage });
}
