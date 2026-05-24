import { NextResponse } from "next/server";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET() {
  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/tenant/config`, {
      cache: "no-store",
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data) {
      return NextResponse.json(
        { name: process.env.NEXT_PUBLIC_TENANT_NAME ?? "Nexus", slug: process.env.NEXT_PUBLIC_TENANT_SLUG ?? "nexus", environment: "development" },
        { status: 200 },
      );
    }
    return NextResponse.json(data, { status: 200 });
  } catch {
    // Tenant config must never block the app — return the safe fallback.
    return NextResponse.json(
      { name: process.env.NEXT_PUBLIC_TENANT_NAME ?? "Nexus", slug: process.env.NEXT_PUBLIC_TENANT_SLUG ?? "nexus", environment: "development" },
      { status: 200 },
    );
  }
}
