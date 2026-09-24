import { NextRequest, NextResponse } from "next/server";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { fastapiFetch } from "@/lib/fastapi";

export async function proxyConnectorRequest(request: NextRequest, path = "") {
  const method = request.method;
  try {
    const rawBody = method === "GET" || method === "HEAD" || method === "DELETE"
      ? ""
      : await request.text();
    const body = rawBody.trim() ? JSON.parse(rawBody) : undefined;
    const data = await fastapiFetch<unknown>(
      `/api/v1/connectors${path ? `/${path}` : ""}${request.nextUrl.search}`,
      { method, body, cache: "no-store" },
    );
    return data === undefined
      ? new NextResponse(null, { status: 204 })
      : NextResponse.json(data, { status: method === "POST" && !path.includes("health") ? 201 : 200 });
  } catch (error) {
    return upstreamErrorResponse(
      `admin/connectors ${method}`,
      error,
      "Connector request failed",
    );
  }
}
