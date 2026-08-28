import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    // Approval now carries the role, project scope and welcome note the
    // reviewer chose. Read defensively: a caller that sends no body must keep
    // working, and acceptance then applies the default member role.
    // `fastapiFetch` serializes `body` itself — passing a string here would
    // double-encode it into a JSON string literal.
    const body = await request.json().catch(() => ({}));
    const data = await fastapiFetch(`/api/v1/admin/access-requests/${id}/approve`, {
      method: "POST",
      body,
    });
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to approve request";
    const status = message.includes("502")
      ? 502
      : message.includes("409")
        ? 409
        : message.includes("403")
          ? 403
          : message.includes("400")
            ? 400
            : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
