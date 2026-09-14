import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET as docsProjects } from "@/app/api/docs/projects/route";
import { GET as userProjects } from "@/app/api/projects/route";
import { GET as deepDiveProjects } from "@/app/api/deep-dive/projects/route";

/**
 * The proxy must not flatten the upstream status.
 *
 * These routes sit in the middle of the "randomly logged out" chain.
 * `middleware.ts` deliberately admits a request carrying only a
 * refresh_token; the proxy then forwards an access token the backend has
 * already expired and gets a 401 back. The old handlers caught that and
 * returned a flat 500, so the browser could not tell an auth refusal (rotate
 * the token and replay) from a server defect (nothing to do) from a backend
 * outage (retry shortly) — and the selector rendered "No projects available"
 * for a perfectly valid session.
 */

const ROUTES = [
  { name: "docs/projects", handler: docsProjects, path: "/api/docs/projects" },
  { name: "projects", handler: userProjects, path: "/api/projects" },
  { name: "deep-dive/projects", handler: deepDiveProjects, path: "/api/deep-dive/projects" },
] as const;

function request(path: string) {
  return new NextRequest(`http://localhost:3000${path}`, {
    headers: { cookie: "access_token=expired; refresh_token=valid" },
  });
}

describe.each(ROUTES)("GET $name", ({ handler, path }) => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("forwards a 401 as a 401", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Not authenticated" }), { status: 401 }),
    );

    const response = await handler(request(path));

    expect(response.status).toBe(401);
    const body = (await response.json()) as { error: string; retryable: boolean };
    expect(body.error).toBe("Not authenticated");
    // Retrying with the same expired token cannot help; rotating can.
    expect(body.retryable).toBe(false);
  });

  it("forwards a 403 as a 403, distinct from an auth refusal", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Not permitted" }), { status: 403 }),
    );

    const response = await handler(request(path));
    expect(response.status).toBe(403);
  });

  it("reports an unreachable backend as 503, not 500", async () => {
    // 503 says "try again". A 500 reads as a defect in the request itself and
    // clients must not confuse either with 401.
    vi.spyOn(global, "fetch").mockRejectedValue(new TypeError("fetch failed"));

    const response = await handler(request(path));

    expect(response.status).toBe(503);
    expect(response.headers.get("Retry-After")).toBe("5");
    const body = (await response.json()) as { retryable: boolean };
    expect(body.retryable).toBe(true);
  });
});

describe("GET docs/projects payload", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("still validates and totals a successful response", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          projects: [
            { id: "p1", display_name: "Acme", platform: "talend", doc_count: 3 },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const response = await docsProjects(request("/api/docs/projects"));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      projects: [{ id: "p1", display_name: "Acme", platform: "talend", doc_count: 3 }],
      total: 1,
    });
  });
});
