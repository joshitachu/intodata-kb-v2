import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { DocumentListResponseSchema } from "@/types";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const skip = searchParams.get("skip") || "0";
    const limit = searchParams.get("limit") || "50";

    const data = await fastapiFetch(
      `/api/v1/documents?skip=${skip}&limit=${limit}`,
    );

    const validated = DocumentListResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    return upstreamErrorResponse("documents GET", error, "Failed to fetch documents");
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const id = searchParams.get("id");

    if (!id) {
      return NextResponse.json({ error: "Document ID required" }, { status: 400 });
    }

    await fastapiFetch(`/api/v1/documents/${id}`, { method: "DELETE" });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    return upstreamErrorResponse("documents DELETE", error, "Delete failed");
  }
}
