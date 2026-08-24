import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/invite/validate", {
    context: "auth/invite/validate",
    fallbackError: "Invalid invite",
    forwardBody: true,
  });
}
