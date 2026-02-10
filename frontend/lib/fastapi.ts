const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

interface FetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

export async function fastapiFetch<T>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { body, headers, ...rest } = options;

  const res = await fetch(`${FASTAPI_URL}${path}`, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": "1", // Phase 1: hardcoded user
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
  const res = await fetch(`${FASTAPI_URL}${path}`, {
    method: "POST",
    headers: {
      "X-User-Id": "1", // Phase 1: hardcoded user
    },
    body: formData,
  });

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`FastAPI ${res.status}: ${detail}`);
  }

  return res.json() as Promise<T>;
}
