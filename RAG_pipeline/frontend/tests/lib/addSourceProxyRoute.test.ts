import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "@/app/api/source/github/route";

/**
 * The /api/source/github -> /mcp/add-repo proxy must forward EVERY credential
 * reference its schema accepts.
 *
 * Written because `azure_devops_connection_id` was added to the zod schema and to
 * the client, but not to this handler's forwarded body. Nothing failed: the field
 * was parsed and silently dropped, `/mcp/add-repo` fell through to an
 * unauthenticated clone, and Azure DevOps answered "Authentication failed" -- a
 * credential error for what was really a lost field. Neither side's tests cover
 * this seam (the backend tests post to /mcp/add-repo directly; the panel test
 * asserts only what the client was called with), so it lives here.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Capture what the handler sends upstream. */
function captureUpstream() {
  const fetchMock = vi.fn().mockResolvedValue({
    status: 200,
    text: async () => JSON.stringify({ id: "proj" }),
    headers: { forEach: () => undefined, has: () => false, set: () => undefined },
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function post(body: Record<string, unknown>) {
  return POST(
    new NextRequest("http://localhost:3000/api/source/github", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

const BASE = {
  repo_url: "https://dev.azure.com/acme/Data/_git/etl",
  project_id: "etl",
  platform: "talend",
};

async function forwardedBody(fetchMock: ReturnType<typeof captureUpstream>) {
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toContain("/mcp/add-repo");
  return JSON.parse(String((init as RequestInit).body));
}

describe("POST /api/source/github -> /mcp/add-repo", () => {
  it("forwards the Azure DevOps connection id", async () => {
    const fetchMock = captureUpstream();
    const response = await post({
      ...BASE,
      azure_devops_connection_id: "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    });

    expect(response.status).toBe(200);
    expect(await forwardedBody(fetchMock)).toMatchObject({
      url: BASE.repo_url,
      project_id: "etl",
      platform: "talend",
      azure_devops_connection_id: "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    });
  });

  it("forwards a GitHub App installation id", async () => {
    const fetchMock = captureUpstream();
    await post({
      ...BASE,
      repo_url: "https://github.com/acme/repo",
      installation_id: 4242,
    });

    expect(await forwardedBody(fetchMock)).toMatchObject({ installation_id: 4242 });
  });

  it("forwards a raw PAT", async () => {
    const fetchMock = captureUpstream();
    await post({
      ...BASE,
      repo_url: "https://github.com/acme/repo",
      github_token: "ghp_token",
    });

    expect(await forwardedBody(fetchMock)).toMatchObject({ github_token: "ghp_token" });
  });

  it("omits credential fields that were not supplied rather than sending null", async () => {
    const fetchMock = captureUpstream();
    await post(BASE);

    const forwarded = await forwardedBody(fetchMock);
    expect(forwarded).not.toHaveProperty("github_token");
    expect(forwarded).not.toHaveProperty("installation_id");
    expect(forwarded).not.toHaveProperty("azure_devops_connection_id");
  });

  it("rejects a malformed body without calling upstream", async () => {
    const fetchMock = captureUpstream();
    const response = await post({ repo_url: "not-a-url", project_id: "x", platform: "talend" });

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
