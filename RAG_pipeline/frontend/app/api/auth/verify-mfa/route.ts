import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/verify-mfa", {
    context: "auth/verify-mfa",
    fallbackError: "MFA verification failed",
    forwardBody: true,
  });
}
