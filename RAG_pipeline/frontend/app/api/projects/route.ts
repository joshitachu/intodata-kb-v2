import { NextResponse, type NextRequest } from "next/server";
import { UserProjectsResponseSchema } from "@/types";
import { FASTAPI_URL } from "@/lib/fastapi";
import { fetchBackend, forwardSetCookies, upstreamErrorResponse } from "@/lib/backendProxy";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { BackendResponseError } from "@/lib/upstreamErrors";

/**
 * Project list for the retrieval-scope selector.
 *
 * Same reasoning as `app/api/docs/projects/route.ts`: the upstream status is
 * the client's only signal that it should rotate its token rather than retry,
 * so it is forwarded unchanged instead of being flattened to 500.
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

    const validated = UserProjectsResponseSchema.parse(await res.json());
    return forwardSetCookies(res, NextResponse.json(validated));
  } catch (error) {
    return upstreamErrorResponse("projects GET", error, "Failed to load projects");
  }
}
