import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";

interface RouteParams {
  params: Promise<{ id: string; proposalId: string }>;
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  try {
    const { id, proposalId } = await params;
    const body = await request.json() as Record<string, unknown>;
    if (!Number.isInteger(body.expected_version) || !["approve", "reject"].includes(String(body.decision))) {
      return NextResponse.json({ error: "Invalid approval decision" }, { status: 400 });
    }
    const upstream = await fetch(
      `${FASTAPI_URL}/api/v1/conversations/${encodeURIComponent(id)}/approvals/${encodeURIComponent(proposalId)}/decision`,
      {
        method: "POST",
        headers: backendProxyHeaders(request, { json: true }),
        body: JSON.stringify(body),
      },
    );
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(
      upstream.ok ? data : { error: typeof data.detail === "string" ? data.detail : "Approval could not be processed" },
      { status: upstream.status },
    );
  } catch {
    return NextResponse.json({ error: "Invalid approval decision" }, { status: 400 });
  }
}
