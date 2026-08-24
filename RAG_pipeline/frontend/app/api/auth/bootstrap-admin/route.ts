import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  // Forwards Set-Cookie so the newly bootstrapped admin is logged in immediately.
  return proxyJson(request, "/api/v1/auth/bootstrap-admin", {
    context: "auth/bootstrap-admin",
    fallbackError: "Bootstrap failed",
    forwardBody: true,
  });
}
