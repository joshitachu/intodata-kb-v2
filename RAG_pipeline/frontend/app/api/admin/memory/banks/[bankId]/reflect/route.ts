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
    if (sp.has("query")) qs.set("query", sp.get("query")!);
    const query = qs.toString() ? `?${qs.toString()}` : "";
    const data = await fastapiFetch(
      `/api/v1/memory/banks/${encodeURIComponent(bankId)}/reflect${query}`,
    );
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("admin/memory/banks/[bankId]/reflect GET", error, "Failed to reflect");
  }
}
