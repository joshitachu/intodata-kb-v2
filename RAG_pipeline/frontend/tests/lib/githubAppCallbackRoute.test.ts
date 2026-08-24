import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "@/app/api/source/github/callback/route";

afterEach(() => {
  vi.unstubAllGlobals();
});

function upstream(status: number, body = "{}") {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status, text: async () => body }));
}

/**
 * Route the two server-side calls this handler makes: the MCP callback and, when
 * that answers 401, the session refresh.
 */
function upstreamWithRefresh(options: {
  mcpStatuses: number[];
  refreshOk: boolean;
  setCookies?: string[];
}) {
  const mcpStatuses = [...options.mcpStatuses];
  const fetchMock = vi.fn(async (url: string) => {
    if (String(url).includes("/auth/refresh")) {
      return {
        ok: options.refreshOk,
        status: options.refreshOk ? 200 : 401,
        text: async () => "{}",
        headers: { getSetCookie: () => options.setCookies ?? [] },
      };
    }
    const status = mcpStatuses.shift() ?? 500;
    return { status, text: async () => JSON.stringify({ detail: "Not authenticated" }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/**
 * Behind nginx + Docker the request URL Next.js sees is the container-internal
 * one, so an absolute redirect built from the request origin sent the browser to
 * https://0.0.0.0:3000/... The Location must stay relative.
 */
describe("GitHub App callback redirect", () => {
  it("redirects relatively when the request arrives on the container host", async () => {
    upstream(200);
    const request = new NextRequest("http://0.0.0.0:3000/api/source/github/callback?installation_id=155193510&code=abc");

    const response = await GET(request);

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "/settings/sources?installation_id=155193510&connected=1",
    );
  });

  it("passes the upstream failure status through as connect_error", async () => {
    upstream(403);
    const request = new NextRequest("https://client.example.com/api/source/github/callback?installation_id=1");

    const response = await GET(request);

    // No usable detail in the body -> status only, no empty message parameter.
    expect(response.headers.get("location")).toBe("/settings/sources?connect_error=403");
  });

  it("carries the upstream reason so the operator can act on it", async () => {
    upstream(400, JSON.stringify({ detail: "GitHub did not send an authorization code" }));
    const request = new NextRequest("https://client.example.com/api/source/github/callback?installation_id=1");

    const location = (await GET(request)).headers.get("location")!;
    const query = new URLSearchParams(location.split("?")[1]);

    expect(query.get("connect_error")).toBe("400");
    expect(query.get("connect_message")).toBe("GitHub did not send an authorization code");
  });

  it("refreshes an expired session and retries instead of failing with 401", async () => {
    // Installing an App often takes longer than the 60-minute access token.
    const fetchMock = upstreamWithRefresh({
      mcpStatuses: [401, 200],
      refreshOk: true,
      setCookies: ["access_token=new; Path=/; HttpOnly", "refresh_token=rotated; Path=/; HttpOnly"],
    });
    const request = new NextRequest(
      "https://client.example.com/api/source/github/callback?installation_id=155193510",
      { headers: { cookie: "access_token=stale; refresh_token=valid" } },
    );

    const response = await GET(request);

    expect(response.headers.get("location")).toBe(
      "/settings/sources?installation_id=155193510&connected=1",
    );
    // The retry must carry the refreshed token, not the stale one.
    const retryCall = fetchMock.mock.calls[2] as unknown as [string, RequestInit];
    const retryCookie = retryCall[1].headers as Record<string, string>;
    expect(retryCookie.Cookie).toContain("access_token=new");
    // The rotated refresh token must reach the browser or the next request logs it out.
    expect(response.headers.getSetCookie().join(";")).toContain("refresh_token=rotated");
  });

  it("explains the expired session when the refresh also fails", async () => {
    upstreamWithRefresh({ mcpStatuses: [401], refreshOk: false });
    const request = new NextRequest(
      "https://client.example.com/api/source/github/callback?installation_id=1",
      { headers: { cookie: "access_token=stale" } },
    );

    const query = new URLSearchParams((await GET(request)).headers.get("location")!.split("?")[1]);

    expect(query.get("connect_error")).toBe("401");
    expect(query.get("connect_message")).toBeTruthy();
  });

  it("reports a transport failure instead of claiming a connection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    const request = new NextRequest("https://client.example.com/api/source/github/callback?installation_id=1");

    const response = await GET(request);
    const query = new URLSearchParams(response.headers.get("location")!.split("?")[1]);

    expect(query.get("connect_error")).toBe("502");
    expect(query.get("connect_message")).toContain("could not reach the MCP server");
  });
});
