import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/login", {
    context: "auth/login",
    fallbackError: "Login failed",
    forwardBody: true,
  });
}
