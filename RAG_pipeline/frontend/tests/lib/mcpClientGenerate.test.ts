import { afterEach, describe, expect, it, vi } from "vitest";
import { CUSTOMER_DOC_GENERATION_ROUTE, generateDocs, McpRequestError, listGithubAppRepos } from "@/lib/mcpClient";

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    text: async () => "",
    json: async () => ({}),
    ...response,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("generateDocs", () => {
  it("pins doc generation to the legacy custom harness on Anthropic Sonnet", async () => {
    const fetchMock = mockFetch({});
    await generateDocs("acme-talend");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/source/generate");
    const body = JSON.parse(String(init.body));
    expect(body).toEqual({
      project_id: "acme-talend",
      resume: true,
      doc_generation_provider: "custom",
      investigation_model: "anthropic/claude-sonnet-4-6",
      model: "anthropic/claude-sonnet-4-6",
      write_model: "anthropic/claude-sonnet-4-6",
    });
  });

  it("sends no fields beyond the pinned route (GenerateRequest forbids extras)", async () => {
    const fetchMock = mockFetch({});
    await generateDocs("acme-talend", { resume: false });

    const body = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect(Object.keys(body).sort()).toEqual(
      ["project_id", "resume", ...Object.keys(CUSTOMER_DOC_GENERATION_ROUTE)].sort(),
    );
    expect(body.resume).toBe(false);
  });

  it("forwards a chosen pack as a per-run override", async () => {
    const fetchMock = mockFetch({});
    await generateDocs("acme-talend", { pluginPackId: 7 });

    const body = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect(body.plugin_pack_id).toBe(7);
  });

  it("omits plugin_pack_id when no pack was chosen, so the project default stands", async () => {
    // Sending `null` would mean "override the project's pack with no pack at
    // all", silently discarding a default the user never touched.
    const fetchMock = mockFetch({});
    await generateDocs("acme-talend", { pluginPackId: null });

    const body = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect("plugin_pack_id" in body).toBe(false);
  });
});

describe("listGithubAppRepos", () => {
  it("surfaces the upstream status so a removed installation is distinguishable", async () => {
    mockFetch({ ok: false, status: 404, text: async () => "gone" });

    await expect(listGithubAppRepos(42)).rejects.toMatchObject({
      name: "McpRequestError",
      status: 404,
    });
  });

  it("reports a GitHub outage as a retryable 502, not a removed installation", async () => {
    mockFetch({ ok: false, status: 502, text: async () => "upstream" });

    const error = await listGithubAppRepos(42).catch((err: unknown) => err);
    expect(error).toBeInstanceOf(McpRequestError);
    expect((error as McpRequestError).status).toBe(502);
  });
});
