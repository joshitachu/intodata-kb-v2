import type { UseMutationResult } from "@tanstack/react-query";

interface Props {
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
  gitManualReviewReason: string | null;
  gitReason: string | null;
  canRepairGitUpdate: boolean;
  repairLatestGitUpdate: UseMutationResult<unknown, Error, string, unknown>;
  projectId: string;
}

export default function GitUpdateSummary({
  gitAction,
  gitStrategy,
  gitConfidence,
  gitChangedFiles,
  gitDisplayDocs,
  gitToolCallCount,
  gitRepairAttempts,
  gitFallbackUsed,
  gitRequiredFacts,
  gitStaleClaims,
  gitManualReviewReason,
  gitReason,
  canRepairGitUpdate,
  repairLatestGitUpdate,
  projectId,
}: Props) {
  return (
    <div className="text-sm bg-surface-raised rounded px-3 py-2 text-slate-400 border border-white/[0.07]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span>Action: <span className="font-mono text-slate-300">{gitAction ?? "None"}</span></span>
        {gitStrategy && <span>Strategy: <span className="font-mono text-slate-300">{gitStrategy}</span></span>}
        {gitConfidence !== null && <span>Confidence: <span className="font-mono text-slate-300">{Math.round(gitConfidence * 100)}%</span></span>}
        <span>Changed files: <span className="font-mono text-slate-300">{gitChangedFiles.length}</span></span>
        <span>Touched docs: <span className="font-mono text-slate-300">{gitDisplayDocs.length}</span></span>
        <span>Tools: <span className="font-mono text-slate-300">{gitToolCallCount}</span></span>
        <span>Repairs: <span className="font-mono text-slate-300">{gitRepairAttempts}</span></span>
        {gitFallbackUsed && <span className="text-amber-400">Fallback used</span>}
        {canRepairGitUpdate && (
          <button
            type="button"
            className="ml-auto btn-dark text-xs px-2 py-1"
            disabled={repairLatestGitUpdate.isPending}
            onClick={() => repairLatestGitUpdate.mutate(projectId)}
          >
            {repairLatestGitUpdate.isPending ? "Repairing..." : "Repair Latest"}
          </button>
        )}
      </div>
      {gitDisplayDocs.length > 0 && (
        <div className="mt-1 font-mono text-xs text-slate-500 truncate" title={gitDisplayDocs.join(", ")}>
          {gitDisplayDocs.join(", ")}
        </div>
      )}
      {(gitRequiredFacts.length > 0 || gitStaleClaims.length > 0) && (
        <div className="mt-1 text-xs text-slate-500 truncate" title={[...gitRequiredFacts, ...gitStaleClaims].join(" | ")}>
          Facts {gitRequiredFacts.length} · stale claims {gitStaleClaims.length}
        </div>
      )}
      {(gitManualReviewReason || gitReason) && (
        <div className="mt-1 text-xs text-amber-400 truncate" title={gitManualReviewReason || gitReason || ""}>
          {gitManualReviewReason || gitReason}
        </div>
      )}
    </div>
  );
}
