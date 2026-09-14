import { NextRequest, NextResponse } from "next/server";
import { AddSourceResponseSchema } from "@/types";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import { KB_API_URL, safeProjectId } from "../../../_proxy";

export const dynamic = "force-dynamic";

// Extraction happens upstream inside the request, so this shares the ceiling of
// the initial upload route rather than the default.
export const maxDuration = 300;

export const config = {
  api: {
    bodyParser: false,
    responseLimit: false,
  },
  maxDuration: 300,
};

/**
 * Add a ZIP sub-project to a project that already has one.
 *
 * Deliberately a separate route from `/api/source/upload-zip`: that one creates
 * or replaces a project's whole tree, this one only appends a sub-project and
 * leaves everything already generated in place. The body is streamed through as
 * multipart -- the size limit, the zip-slip-safe extraction and the flat-source
 * migration are all upstream concerns and are not reimplemented here.
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const projectId = safeProjectId(decodeURIComponent((await params).projectId));
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }

  try {
    const cookieHeader = request.headers.get("cookie") || "";
    const formData = await request.formData();
    const file = formData.get("file");

    if (!file || !(file instanceof Blob)) {
      return NextResponse.json({ error: "File is required" }, { status: 400 });
    }

    const upstreamForm = new FormData();
    const filename =
      typeof File !== "undefined" && file instanceof File ? file.name : "source.zip";
    upstreamForm.append("file", file, filename);

    const upstream = await fetch(`${KB_API_URL}/mcp/projects/${projectId}/sources`, {
      method: "POST",
      headers: { Cookie: cookieHeader },
      body: upstreamForm,
    });

    const text = await upstream.text();
    if (!upstream.ok) {
      // Pass the upstream body through verbatim: "this project is not a ZIP
      // project" and the size-limit message are both user-facing.
      return new Response(text, {
        status: upstream.status,
        headers: {
          "Content-Type": upstream.headers.get("Content-Type") || "application/json",
        },
      });
    }

    // The panel needs `subproject` to name the new row and
    // `migrated_existing_source` to explain why the original tree moved, so both
    // go through the schema rather than an untyped passthrough.
    return NextResponse.json(AddSourceResponseSchema.parse(JSON.parse(text)));
  } catch (error) {
    return upstreamErrorResponse("source/projects/[projectId]/sources POST", error, "Adding the source failed");
  }
}
