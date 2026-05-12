import { NextRequest, NextResponse } from "next/server";

const KB_API_URL = process.env.KB_API_URL || "http://localhost:8002";

export const dynamic = "force-dynamic";

export const config = {
  api: {
    bodyParser: false,
    responseLimit: false,
  },
  maxDuration: 300,
};

export async function POST(request: NextRequest) {
  try {
    const cookieHeader = request.headers.get("cookie") || "";
    const formData = await request.formData();
    const file = formData.get("file");
    const projectId = formData.get("project_id");
    const platform = formData.get("platform");

    if (!file || !(file instanceof Blob)) {
      return NextResponse.json({ error: "File is required" }, { status: 400 });
    }
    if (!projectId || typeof projectId !== "string") {
      return NextResponse.json({ error: "Project is required" }, { status: 400 });
    }
    if (!platform || typeof platform !== "string") {
      return NextResponse.json({ error: "Platform is required" }, { status: 400 });
    }

    const upstreamForm = new FormData();
    const filename =
      typeof File !== "undefined" && file instanceof File ? file.name : "source.zip";
    upstreamForm.append("file", file, filename);
    upstreamForm.append("project_id", projectId);
    upstreamForm.append("platform", platform);

    const upstream = await fetch(`${KB_API_URL}/mcp/upload-zip`, {
      method: "POST",
      headers: { Cookie: cookieHeader },
      body: upstreamForm,
    });

    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") || "application/json",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Upload failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
