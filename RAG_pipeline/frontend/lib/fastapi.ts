import { cookies, headers } from "next/headers";
import { clientAddressHeaders } from "@/lib/proxyHeaders";

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

export async function fastapiFetch<T>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { body, headers, ...rest } = options;
  const authHeaders = await getAuthHeaders();

  const res = await fetch(`${FASTAPI_URL}${path}`, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders,
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`FastAPI ${res.status}: ${detail}`);
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

  const res = await fetch(`${FASTAPI_URL}${path}`, {
    method: "POST",
    headers: {
      ...authHeaders,
    },
    body: formData,
  });

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`FastAPI ${res.status}: ${detail}`);
  }

  return res.json() as Promise<T>;
}
