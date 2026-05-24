import { NextResponse } from "next/server";
import { FASTAPI_URL } from "@/lib/fastapi";

export async function GET() {
  try {
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/bootstrap-status`, {
      method: "GET",
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json({ needs_bootstrap: false }, { status: res.status });
    }
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ needs_bootstrap: false }, { status: 500 });
  }
}
