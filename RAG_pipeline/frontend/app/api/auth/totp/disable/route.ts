import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/totp/disable", {
    context: "auth/totp/disable",
    fallbackError: "TOTP disable failed",
    forwardBody: true,
  });
}
