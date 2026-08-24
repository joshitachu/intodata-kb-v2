import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/otp/verify", {
    context: "auth/otp/verify",
    fallbackError: "Code verification failed",
    forwardBody: true,
  });
}
