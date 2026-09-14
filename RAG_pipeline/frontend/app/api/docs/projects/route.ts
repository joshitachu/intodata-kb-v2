import { NextResponse, type NextRequest } from "next/server";
import { DocsProjectsResponseSchema } from "@/types";
import { FASTAPI_URL } from "@/lib/fastapi";
import { fetchBackend, forwardSetCookies, upstreamErrorResponse } from "@/lib/backendProxy";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { BackendResponseError } from "@/lib/upstreamErrors";

/**
 * Documentation project list.
 *
 * On `lib/backendProxy` rather than `fastapiFetch` because the status is
 * load-bearing here. This route sits in the middle of the "randomly logged
 * out" chain: `middleware.ts` admits a request that carries only a
 * `refresh_token`, this proxy forwards the already-expired access token, the
 * backend answers 401 — and the old `catch { return 500 }` turned that into a
 * generic server error. The browser could then neither refresh the token (it
 * could not see the refusal was an auth refusal) nor sensibly retry, so the
 * selector rendered "No projects available" for a valid session.
 *
 * Set-Cookie is forwarded in both directions: the backend clears session
 * cookies on a real 401, and dropping those headers leaves stale cookies that
 * keep `middleware.ts` admitting a dead session.
 */
export async function GET(request: NextRequest) {
  try {
    const res = await fetchBackend(`${FASTAPI_URL}/api/v1/projects`, {
      headers: backendProxyHeaders(request),
      cache: "no-store",
    });

    if (!res.ok) {
      throw new BackendResponseError(res.status, await res.text(), "/api/v1/projects");
    }

    const data = (await res.json()) as {
      projects: Array<{ id: string; display_name: string; platform: string; doc_count: number }>;
    };
    const validated = DocsProjectsResponseSchema.parse({
      projects: data.projects,
      total: data.projects.length,
    });
    return forwardSetCookies(res, NextResponse.json(validated));
  } catch (error) {
    return upstreamErrorResponse("docs/projects GET", error, "Failed to fetch projects");
  }
}
