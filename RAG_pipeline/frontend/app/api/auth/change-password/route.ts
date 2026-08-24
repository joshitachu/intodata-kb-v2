import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/change-password", {
    context: "auth/change-password",
    fallbackError: "Password change failed",
    forwardBody: true,
  });
}
