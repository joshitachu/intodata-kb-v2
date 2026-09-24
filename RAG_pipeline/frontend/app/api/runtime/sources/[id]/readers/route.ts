import { NextRequest, NextResponse } from "next/server";
import { backendProxyHeaders } from "@/lib/proxyHeaders";
import { FASTAPI_URL } from "@/lib/fastapi";
import {
  RuntimeSourceReadGrantListSchema,
  RuntimeSourceReadGrantResponseSchema,
} from "@/types/runtime";

/**
 * Which projects are attached to this source, and attaching another.
 *
 * A vendor tenant is registered exactly once and mirrored once; every project
 * that may read it is an attachment over that single mirror, rather than a
 * second registration that would poll the vendor twice and let two mirrors of
 * one estate drift apart. A source has no owning project, so these rows are the
 * complete answer to "who reaches this tenant".
 *
 * Distinct from `workspace-map`, which SPLITS a source's rows between projects
 * at write time. This SHARES the same rows with another project.
 *
 * Both payloads are validated against the full backend contract rather than
 * forwarded blind. Zod strips anything it was not told about, and a stripped
 * field is `undefined` in the browser with nothing logged anywhere -- so the
 * schema must stay complete. That is also why the `source_project_id` ->
 * `source_estate_key` rename had to land in `types/runtime.ts` at the same
 * time: until it did, every response here failed the parse and 502'd.
 * `exposes_project_ids` in particular is the only signal that an attachment
 * reaches beyond the source and the project named on it.
 */
export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/sources/${encodeURIComponent(id)}/readers`,
    { headers: backendProxyHeaders(request), cache: "no-store" },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return NextResponse.json(data, { status: res.status });
  const parsed = RuntimeSourceReadGrantListSchema.safeParse(data);
  if (!parsed.success) {
    // 502, not 500: the backend answered, its answer did not match the
    // contract. Reported rather than partially rendered, because the field most
    // likely to be missing is the one that warns about extra exposure.
    return NextResponse.json(
      { error: "The runtime service returned an unexpected sharing response." },
      { status: 502 },
    );
  }
  return NextResponse.json(parsed.data, { status: res.status });
}

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await request.text();
  const res = await fetch(
    `${FASTAPI_URL}/api/v1/runtime/sources/${encodeURIComponent(id)}/readers`,
    {
      method: "POST",
      headers: {
        ...backendProxyHeaders(request),
        "content-type": request.headers.get("content-type") ?? "application/json",
      },
      body,
      cache: "no-store",
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return NextResponse.json(data, { status: res.status });
  const parsed = RuntimeSourceReadGrantResponseSchema.safeParse(data);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "The runtime service returned an unexpected sharing response." },
      { status: 502 },
    );
  }
  return NextResponse.json(parsed.data, { status: res.status });
}
