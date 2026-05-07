import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const cookieStore = await cookies();
    const token = cookieStore.get("access_token");
    const res = await fetch(`${FASTAPI_URL}/api/v1/auth/totp/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Cookie: `access_token=${token.value}` } : {}),
      },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      return NextResponse.json(
        { error: data.detail || "TOTP verify failed" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "TOTP verify failed" }, { status: 500 });
  }
}
