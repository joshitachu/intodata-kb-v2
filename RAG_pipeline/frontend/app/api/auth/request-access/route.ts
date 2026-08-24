import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/request-access", {
    context: "auth/request-access",
    fallbackError: "Request failed",
    forwardBody: true,
  });
}
