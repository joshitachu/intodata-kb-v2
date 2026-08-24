import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/backendProxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/auth/set-password", {
    context: "auth/set-password",
    fallbackError: "Password creation failed",
    forwardBody: true,
  });
}
