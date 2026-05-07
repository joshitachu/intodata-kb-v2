import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function POST(_request: NextRequest) {
  try {
    const cookieStore = await cookies();
    const token = cookieStore.get("access_token");
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/totp/setup`, {
      method: "POST",
      headers: token ? { Cookie: `access_token=${token.value}` } : {},
    });
    const data = await res.json();
    if (!res.ok) {
      return NextResponse.json(
        { error: data.detail || "TOTP setup failed" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "TOTP setup failed" }, { status: 500 });
  }
}
