import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/totp/setup", {
    context: "auth/totp/setup",
    fallbackError: "TOTP setup failed",
  });
}
