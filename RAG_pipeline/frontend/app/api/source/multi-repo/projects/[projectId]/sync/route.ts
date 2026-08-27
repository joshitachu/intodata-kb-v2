import { NextRequest, NextResponse } from "next/server";
import { proxyMcpJsonSanitized, safeProjectId } from "../../../../_proxy";

export const dynamic = "force-dynamic";
// A full-project sync fast-forwards every member.
export const maxDuration = 800;

/**
 * Fast-forwards member clones.
 *
 * `repo_identity` restricts the sync to one repository -- the incremental path,
 * and the manual equivalent of the push webhook until that is wired. Omitting it
 * syncs every member.
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const projectId = safeProjectId((await params).projectId);
  if (!projectId) {
    return NextResponse.json({ error: "Invalid project id" }, { status: 400 });
  }
  const repoIdentity = request.nextUrl.searchParams.get("repo_identity");
  // Bounded, and free of CONTROL characters only. Spaces and hyphens must
  // stay legal: Azure DevOps project names routinely contain both, and this
  // value is compared against a stored identity, never interpolated into a
  // command. An earlier `[ -]` guard here was a space-to-hyphen RANGE and
  // would have rejected `acme/My Project/repo` and `acme/Talend/pplus-ais`.
  const hasControlChars = repoIdentity !== null &&
    Array.from(repoIdentity).some((ch) => {
      const code = ch.codePointAt(0) ?? 0;
      return code < 0x20 || code === 0x7f;
    });
  if (repoIdentity !== null && (repoIdentity.length > 500 || hasControlChars)) {
    return NextResponse.json({ error: "Invalid repository identity" }, { status: 400 });
  }
  const query = repoIdentity
    ? `?repo_identity=${encodeURIComponent(repoIdentity)}`
    : "";
  return proxyMcpJsonSanitized(
    request,
    `/mcp/multi-repo/projects/${projectId}/sync${query}`,
    { method: "POST" },
  );
}
