import { NextRequest, NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { safeProjectId } from "@/app/api/source/_proxy";
import {
  PersonPreferencesResponseSchema,
  StylePreferenceSchema,
} from "@/types/guidance";

interface RouteParams {
  params: Promise<{ projectId: string }>;
}

/**
 * The person layer: the calling user's own presentation overlay.
 *
 * The identity is taken from the authenticated session upstream and never from
 * the request, so there is nothing here that could address another user's rows.
 * `StylePreferenceSchema` is applied to the outgoing body for the same reason
 * the backend ignores extras: a `user_id` a client puts in the body is dropped
 * before it can travel.
 */
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { projectId: rawProjectId } = await params;
  const projectId = safeProjectId(rawProjectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  try {
    const data = await fastapiFetch(
      `/api/v1/guidance/projects/${projectId}/preferences`,
    );
    return NextResponse.json(PersonPreferencesResponseSchema.parse(data));
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/projects/[projectId]/preferences GET",
      error,
      "Failed to load your answer preferences",
    );
  }
}

export async function PUT(request: NextRequest, { params }: RouteParams) {
  const { projectId: rawProjectId } = await params;
  const projectId = safeProjectId(rawProjectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "A JSON body is required" }, { status: 400 });
  }

  const parsed = StylePreferenceSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: parsed.error.issues[0]?.message ?? "Invalid answer preferences" },
      { status: 400 },
    );
  }

  try {
    const data = await fastapiFetch(
      `/api/v1/guidance/projects/${projectId}/preferences`,
      { method: "PUT", body: parsed.data },
    );
    return NextResponse.json(PersonPreferencesResponseSchema.parse(data));
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/projects/[projectId]/preferences PUT",
      error,
      "Failed to save your answer preferences",
    );
  }
}
