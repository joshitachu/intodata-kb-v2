import { NextRequest } from "next/server";

export const KB_API_URL = process.env.KB_API_URL || "http://localhost:8002";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

export function safeProjectId(projectId: string): string | null {
  const trimmed = projectId.trim();
  return /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$/.test(trimmed) ? trimmed : null;
}

export async function proxyMcpJson(
  request: NextRequest,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const cookieHeader = request.headers.get("cookie") || "";
  const headers = new Headers(init.headers);
  if (cookieHeader) headers.set("Cookie", cookieHeader);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const upstream = await fetch(`${KB_API_URL}${path}`, {
    ...init,
    headers,
  });

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase()) && key.toLowerCase() !== "content-length") {
      responseHeaders.set(key, value);
    }
  });
  if (!responseHeaders.has("Content-Type")) {
    responseHeaders.set("Content-Type", "application/json");
  }

  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export async function proxyMcpJsonSanitized(
  request: NextRequest,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const raw = await proxyMcpJson(request, path, init);
  const text = await raw.text();
  const headers = new Headers(raw.headers);
  headers.set("Content-Type", "application/json");
  // Remove content-length: secret-stripping changes body size, so we must
  // let Node.js recalculate it from the actual (shorter) body.
  headers.delete("content-length");

  try {
    return new Response(JSON.stringify(stripSourceSecrets(JSON.parse(text))), {
      status: raw.status,
      headers,
    });
  } catch {
    return new Response(text, {
      status: raw.status,
      headers: raw.headers,
    });
  }
}

export function stripSourceSecrets(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripSourceSecrets);
  if (!value || typeof value !== "object") return value;

  const out: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    if (key === "github_token" || key === "github_token_enc" || key === "local_path") {
      continue;
    }
    out[key] = stripSourceSecrets(item);
  }
  return out;
}
