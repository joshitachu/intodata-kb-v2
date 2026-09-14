import { cookies, headers } from "next/headers";
import { clientAddressHeaders } from "@/lib/proxyHeaders";
import { BackendResponseError, UpstreamUnavailableError } from "@/lib/upstreamErrors";

export const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

interface FetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

async function getAuthHeaders(): Promise<Record<string, string>> {
  const forwarded: Record<string, string> = {};
  try {
    // Carry the real client address through to the backend so rate limits and
    // the IP allowlist key on the user, not on this server. See proxyHeaders.ts.
    Object.assign(forwarded, clientAddressHeaders(await headers()));
  } catch {
    // headers() not available in this context - that's fine
  }

  try {
    const cookieStore = await cookies();
    const cookieHeader = cookieStore
      .getAll()
      .map((cookie) => `${cookie.name}=${cookie.value}`)
      .join("; ");
    const csrfToken = cookieStore.get("csrf_token")?.value;
    return {
      ...forwarded,
      ...(cookieHeader ? { Cookie: cookieHeader } : {}),
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
    };
  } catch {
    // cookies() not available in client context - that's fine
  }
  return forwarded;
}

/**
 * Call the backend and throw a *typed* failure.
 *
 * Two distinct outcomes, never conflated:
 * - the backend answered with an error -> `BackendResponseError` carrying its
 *   status, so a route handler can forward 401/403/409 unchanged;
 * - the backend did not answer -> `UpstreamUnavailableError`, which is a 503
 *   ("try again"), not a 500.
 *
 * Pass either to `upstreamErrorResponse()` and the classification is done.
 */
async function callBackend(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${FASTAPI_URL}${path}`, init);
  } catch (error) {
    const aborted = error instanceof Error && error.name === "AbortError";
    throw new UpstreamUnavailableError(path, aborted ? "timeout" : "network", error);
  }
}

export async function fastapiFetch<T>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { body, headers, ...rest } = options;
  const authHeaders = await getAuthHeaders();

  const res = await callBackend(path, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders,
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  if (!res.ok) {
    throw new BackendResponseError(res.status, await res.text(), path);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

export async function fastapiUpload<T>(
  path: string,
  formData: FormData,
): Promise<T> {
  const authHeaders = await getAuthHeaders();

  const res = await callBackend(path, {
    method: "POST",
    headers: {
      ...authHeaders,
    },
    body: formData,
  });

  if (!res.ok) {
    throw new BackendResponseError(res.status, await res.text(), path);
  }

  return res.json() as Promise<T>;
}
