import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ bankId: string }> },
) {
  try {
    const { bankId } = await params;
    const sp = request.nextUrl.searchParams;
    const qs = new URLSearchParams();
    if (sp.has("limit")) qs.set("limit", sp.get("limit")!);
    if (sp.has("offset")) qs.set("offset", sp.get("offset")!);
    if (sp.has("type")) qs.set("type", sp.get("type")!);
    const query = qs.toString() ? `?${qs.toString()}` : "";
    const data = await fastapiFetch(
      `/api/v1/memory/banks/${encodeURIComponent(bankId)}/memories${query}`,
    );
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/memory/banks/[bankId]/memories GET", error, "Failed to fetch memories");
  }
}
