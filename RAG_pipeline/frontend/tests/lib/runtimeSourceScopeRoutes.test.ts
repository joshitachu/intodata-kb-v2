import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST as discover } from "@/app/api/runtime/sources/discover/route";
import { POST as scopes } from "@/app/api/runtime/sources/[id]/scopes/route";
import { PATCH as patchConfig } from "@/app/api/runtime/sources/[id]/config/route";

/**
 * Scope discovery and config editing cross the BFF boundary, and both carry
 * things that must not be mangled: unsaved credentials on the way up, and a
 * source id that has to land on the right backend path.
 *
 * Written alongside the TMC environment-selection flow. The proxy is the seam
 * where a dropped field or a mis-built URL fails silently -- the backend never
 * sees the value and reports a credential error for what is really a lost
 * field, exactly the failure /api/source/github hit before.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

function captureUpstream(payload: unknown = { scopes: [] }, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    status,
    json: async () => payload,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function upstream(fetchMock: ReturnType<typeof captureUpstream>) {
  const [url, init] = fetchMock.mock.calls[0];
  return { url: String(url), init: init as RequestInit };
}

describe("POST /api/runtime/sources/discover", () => {
  it("forwards the provider key and the unsaved credentials verbatim", async () => {
    const fetchMock = captureUpstream({
      scopes: [{ id: "env-prod", label: "PROD", detail: "12 tasks", default_selected: true }],
    });
    const body = {
      provider_key: "tmc",
      credentials: {
        api_token: "tmc-token",
        base_url: "https://api.eu.cloud.talend.com",
      },
    };

    const response = await discover(
      new NextRequest("http://localhost:3000/api/runtime/sources/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );

    const { url, init } = upstream(fetchMock);
    expect(url).toContain("/api/v1/runtime/sources/discover");
    expect(JSON.parse(String(init.body))).toEqual(body);
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      scopes: [{ id: "env-prod", label: "PROD" }],
    });
  });

  it("passes a failed connection test through with its status", async () => {
    captureUpstream({ detail: "401 Unauthorized" }, 400);

    const response = await discover(
      new NextRequest("http://localhost:3000/api/runtime/sources/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider_key: "tmc", credentials: {} }),
      }),
    );

    expect(response.status).toBe(400);
  });
});

describe("POST /api/runtime/sources/[id]/scopes", () => {
  it("targets the source's scopes path", async () => {
    const fetchMock = captureUpstream();

    await scopes(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/scopes", { method: "POST" }),
      { params: Promise.resolve({ id: "36" }) },
    );

    const { url, init } = upstream(fetchMock);
    expect(url).toContain("/api/v1/runtime/sources/36/scopes");
    expect(init.method).toBe("POST");
  });
});

describe("PATCH /api/runtime/sources/[id]/config", () => {
  it("forwards the config patch to the config path, not the source path", async () => {
    const fetchMock = captureUpstream({ source: { id: 36 } });
    const body = { config: { environment_ids: ["env-prod", "env-uat"] } };

    await patchConfig(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
      { params: Promise.resolve({ id: "36" }) },
    );

    const { url, init } = upstream(fetchMock);
    expect(url).toMatch(/\/api\/v1\/runtime\/sources\/36\/config$/);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual(body);
  });
});
