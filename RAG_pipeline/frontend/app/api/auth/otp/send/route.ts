import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/otp/send", {
    context: "auth/otp/send",
    fallbackError: "Could not send the code",
    forwardBody: true,
  });
}
