import { NextRequest } from "next/server";
import { proxyConnectorRequest } from "../_proxy";

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxyConnectorRequest(request, path.map(encodeURIComponent).join("/"));
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
