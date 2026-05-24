import type { Project } from "../types";

export interface GitSummaryData {
  gitAction: string | null;
  gitStrategy: string | null;
  gitConfidence: number | null;
  gitChangedFiles: string[];
  gitDisplayDocs: string[];
  gitToolCallCount: number;
  gitRepairAttempts: number;
  gitFallbackUsed: boolean;
  gitRequiredFacts: string[];
  gitStaleClaims: string[];
  gitReason: string | null;
  gitManualReviewReason: string | null;
  canRepairGitUpdate: boolean;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

export function deriveGitSummary(project: Project): GitSummaryData {
  const s = project.git_update_summary ?? null;
  const gitAction = typeof s?.action === "string" ? s.action : null;
  const gitChangedFiles = toStringArray(s?.changed_files);
  const gitPatchedDocs = toStringArray(s?.patched_docs);
  const gitTouchedDocs = toStringArray(s?.touched_docs);
  const gitAffectedDocs = toStringArray(s?.affected_docs);
  const gitDisplayDocs = gitTouchedDocs.length
    ? gitTouchedDocs
    : gitPatchedDocs.length
      ? gitPatchedDocs
      : gitAffectedDocs;
  const gitToolCallCount = Array.isArray(s?.tool_calls)
    ? s.tool_calls.length
    : typeof s?.tool_call_count === "number"
      ? s.tool_call_count
      : typeof s?.tool_calls === "number"
        ? s.tool_calls
        : 0;
  return {
    gitAction,
    gitStrategy: typeof s?.strategy === "string" ? s.strategy : null,
    gitConfidence: typeof s?.confidence === "number" ? s.confidence : null,
    gitChangedFiles,
    gitDisplayDocs,
    gitToolCallCount,
    gitRepairAttempts: typeof s?.repair_attempts === "number" ? s.repair_attempts : 0,
    gitFallbackUsed: s?.fallback_used === true,
    gitRequiredFacts: toStringArray(s?.required_facts),
    gitStaleClaims: toStringArray(s?.stale_claims),
    gitReason: typeof s?.reason === "string" ? s.reason : null,
    gitManualReviewReason: typeof s?.manual_review_reason === "string" ? s.manual_review_reason : null,
    canRepairGitUpdate:
      project.source_type === "git" &&
      typeof s?.before === "string" &&
      typeof s?.after === "string" &&
      gitAction !== null,
  };
}
