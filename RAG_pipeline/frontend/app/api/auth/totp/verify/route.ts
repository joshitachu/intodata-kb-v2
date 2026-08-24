import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/totp/verify", {
    context: "auth/totp/verify",
    fallbackError: "TOTP verify failed",
    forwardBody: true,
  });
}
