import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";
import {
  DocumentRoutingEntrySchema,
  DocumentRoutingResponseSchema,
} from "@/types/guidance";

/**
 * Accepted upstream shapes, in the order they are tried.
 *
 * The published contract is a bare array of routing rows, but the ceiling above
 * which a document stays search-only has to reach the browser too, and a bare
 * array has nowhere to carry it. Both readings are accepted and both are fully
 * declared -- the alternative is guessing, and a guess that lands on the wrong
 * branch strips the array to nothing with no error raised anywhere.
 */
const UpstreamSchema = z.union([
  z.array(DocumentRoutingEntrySchema),
  DocumentRoutingResponseSchema,
]);

/**
 * Where a document uploaded under each role ends up in the agent's workspace.
 *
 * Open to any authenticated caller, and deliberately so: the routing table is
 * the same for everyone, it is what the upload form needs to tell a user what
 * their file will become *before* they spend the upload, and a user who cannot
 * set a privileged role still benefits from seeing what that role would do.
 */
export async function GET(_request: NextRequest) {
  try {
    const data = await fastapiFetch("/api/v1/guidance/document-routing");
    const parsed = UpstreamSchema.parse(data);

    // Normalized to one envelope so the browser has a single shape to read.
    // When the ceiling arrives per row rather than per response, it is lifted
    // rather than dropped: every row publishes the same number.
    const body = Array.isArray(parsed)
      ? {
          routes: parsed,
          materialize_max_chars:
            parsed.find((route) => route.materialize_max_chars !== null)
              ?.materialize_max_chars ?? null,
        }
      : parsed;

    return NextResponse.json(DocumentRoutingResponseSchema.parse(body));
  } catch (error) {
    return upstreamErrorResponse(
      "guidance/document-routing GET",
      error,
      "Failed to load the document routing table",
    );
  }
}
