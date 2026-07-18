import { NextRequest } from "next/server";
import { proxyHarnessPack } from "@/lib/harnessPacksProxy";

export async function GET(request: NextRequest) {
  return proxyHarnessPack(request, "/policy");
}
