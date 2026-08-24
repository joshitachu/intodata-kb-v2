import { NextRequest } from "next/server";
import { proxyPacks } from "../_proxy";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  return proxyPacks(request, "/prompt-slots");
}
