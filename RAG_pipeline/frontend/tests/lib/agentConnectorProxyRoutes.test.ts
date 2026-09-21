import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET as listConnectors } from "@/app/api/admin/connectors/route";
import { POST as connectorAction, DELETE as deleteConnector } from "@/app/api/admin/connectors/[...path]/route";

afterEach(() => vi.restoreAllMocks());

function request(path: string, method = "GET", body?: unknown) {
  return new NextRequest(`http://localhost:3000${path}`, {
    method,
    headers: { cookie: "access_token=owner", ...(body ? { "content-type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
}

describe("agent connector management proxy", () => {
  it("targets the dedicated connector backend without exposing it directly", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ connectors: [], total: 0 }), { status: 200 }),
    );

    const response = await listConnectors(request("/api/admin/connectors"));

    expect(response.status).toBe(200);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/v1/connectors");
  });

  it("forwards a project-scoped capability binding", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: 9 }), { status: 201 }),
    );
    const body = {
      capability_id: "connector.fixture.search",
      project_ids: ["VW_FULL_LANDSCAPE"],
      enabled: true,
    };

    const response = await connectorAction(
      request("/api/admin/connectors/4/bindings", "POST", body),
      { params: Promise.resolve({ path: ["4", "bindings"] }) },
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/v1/connectors/4/bindings");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual(body);
    expect(response.status).toBe(201);
  });

  it("preserves a no-content delete response", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(new Response(null, { status: 204 }));

    const response = await deleteConnector(
      request("/api/admin/connectors/4", "DELETE"),
      { params: Promise.resolve({ path: ["4"] }) },
    );

    expect(response.status).toBe(204);
    expect(await response.text()).toBe("");
  });

  it("keeps owner authorization failures distinguishable", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Owner access required" }), { status: 403 }),
    );

    const response = await listConnectors(request("/api/admin/connectors"));
    expect(response.status).toBe(403);
  });
});
