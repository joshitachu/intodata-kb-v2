import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/invite/start", {
    context: "auth/invite/start",
    fallbackError: "Setup code could not be sent",
    forwardBody: true,
  });
}
