import { cookies } from "next/headers";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

interface FetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

async function getAuthHeaders(): Promise<Record<string, string>> {
  try {
    const cookieStore = await cookies();
    const token = cookieStore.get("access_token");
    if (token) {
      return { Cookie: `access_token=${token.value}` };
    }
  } catch {
    // cookies() not available in client context — that's fine
  }
  return {};
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
