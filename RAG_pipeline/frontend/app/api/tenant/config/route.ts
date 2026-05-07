import { NextResponse } from "next/server";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function GET() {
  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/tenant/config`, {
      cache: "no-store",
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data) {
      return NextResponse.json(
        { name: "IntoData", slug: "intodata", environment: "development" },
        { status: 200 },
      );
    }
    return NextResponse.json(data, { status: 200 });
  } catch {
    // Tenant config must never block the app — return the safe fallback.
    return NextResponse.json(
      { name: "IntoData", slug: "intodata", environment: "development" },
      { status: 200 },
    );
  }
}
