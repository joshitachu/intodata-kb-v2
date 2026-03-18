import { NextRequest, NextResponse } from "next/server";
import { fastapiUpload } from "@/lib/fastapi";
import { IngestResponseSchema } from "@/types";

export const config = {
  api: {
    bodyParser: false,
    responseLimit: false,
  },
  maxDuration: 300,
};

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    const file = formData.get("file");
    const projectId = formData.get("project_id");

    if (!file || !(file instanceof Blob)) {
      return NextResponse.json({ error: "File is required" }, { status: 400 });
    }

    if (!projectId || typeof projectId !== "string") {
      return NextResponse.json({ error: "Project is required" }, { status: 400 });
    }

    // Forward the FormData to FastAPI
    const upstreamForm = new FormData();
    upstreamForm.append("file", file);
    upstreamForm.append("project_id", projectId);

    const data = await fastapiUpload("/api/v1/ingest", upstreamForm);

    const validated = IngestResponseSchema.parse(data);
    return NextResponse.json(validated);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Ingestion failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
