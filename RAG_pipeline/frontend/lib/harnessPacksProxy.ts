import { NextRequest, NextResponse } from "next/server";
import { FASTAPI_URL } from "@/lib/fastapi";
import { backendProxyHeaders } from "@/lib/proxyHeaders";

export async function proxyHarnessPack(request: NextRequest, path: string, method = "GET") {
  try {
    const hasBody = method !== "GET";
    const body = hasBody ? await request.json() : undefined;
    const upstream = await fetch(`${FASTAPI_URL}/api/v1/harness/packs${path}`, {
      method,
      headers: backendProxyHeaders(request, { json: hasBody }),
      body: hasBody ? JSON.stringify(body) : undefined,
    });
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(
      upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Pack request failed" },
      { status: upstream.status },
    );
  } catch {
    return NextResponse.json({ error: "Invalid Pack request" }, { status: 400 });
  }
}
