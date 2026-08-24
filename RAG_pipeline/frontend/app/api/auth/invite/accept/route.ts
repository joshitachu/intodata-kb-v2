import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/invite/accept", {
    context: "auth/invite/accept",
    fallbackError: "Invite acceptance failed",
    forwardBody: true,
  });
}
