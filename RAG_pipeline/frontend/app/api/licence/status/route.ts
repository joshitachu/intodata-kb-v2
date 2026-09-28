import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi";
import { upstreamErrorResponse } from "@/lib/backendProxy";

export const dynamic = "force-dynamic";

/**
 * The instance's licence status, for the global banner and gated controls.
 *
 * Passed through unvalidated on purpose: the browser parses it with
 * `LicenceStatusSchema` (`lib/licence.ts`), and a second schema here would be
 * one more place for a field to be silently stripped. Failures keep their
 * upstream status -- a 401 must reach the client as a 401, never a 500.
 */
export async function GET() {
  try {
    const data = await fastapiFetch<unknown>("/api/v1/licence/status", { cache: "no-store" });
    return NextResponse.json(data);
  } catch (error) {
    return upstreamErrorResponse("licence/status GET", error, "Failed to read the licence status");
  }
}
