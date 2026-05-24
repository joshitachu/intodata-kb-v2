import type { UseMutationResult } from "@tanstack/react-query";
import { formatAbsoluteDate, timeAgo } from "../lib/timeago";
import type { Project } from "../types";
import type { GitSummaryData } from "../lib/gitSummary";
import GitUpdateSummary from "./GitUpdateSummary";
import GithubIntegrationPanel from "./GithubIntegrationPanel";

interface Props {
  project: Project;
  gitSummary: GitSummaryData;
  repairLatestGitUpdate: UseMutationResult<unknown, Error, string, unknown>;
  newToken: string;
  onNewTokenChange: (value: string) => void;
  registerWebhook: UseMutationResult<unknown, Error, string, unknown>;
  updateGithubToken: UseMutationResult<unknown, Error, { projectId: string; token: string }, unknown>;
}

export default function ProjectMetaGrid({
  project,
  gitSummary,
  repairLatestGitUpdate,
  newToken,
  onNewTokenChange,
  registerWebhook,
  updateGithubToken,
}: Props) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div>
        <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Project ID</label>
        <div className="text-sm font-mono bg-surface-raised rounded px-3 py-2 text-slate-400 border border-white/[0.07]">
          {project.id}
        </div>
      </div>
      <div>
        <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Source Type</label>
        <div className="text-sm bg-surface-raised rounded px-3 py-2 text-slate-400 border border-white/[0.07]">
          {project.source_type}
        </div>
      </div>
      <div>
        <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Local Path</label>
        <div className="text-sm font-mono bg-surface-raised rounded px-3 py-2 text-slate-400 border border-white/[0.07] truncate" title={project.local_path}>
          {project.local_path}
        </div>
      </div>
      {project.repo_url && (
        <div>
          <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Repo URL</label>
          <div className="text-sm font-mono bg-surface-raised rounded px-3 py-2 text-slate-400 border border-white/[0.07] truncate" title={project.repo_url}>
            {project.repo_url}
          </div>
        </div>
      )}
      {project.source_type === "git" && (
        <>
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Auto Re-Ingestion</label>
            <div className="text-sm bg-surface-raised rounded px-3 py-2 text-slate-400 border border-white/[0.07]">
              {project.auto_reingest_enabled ? "Enabled" : "Disabled"}
            </div>
          </div>
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Last Checked</label>
            <div
              className="text-sm bg-surface-raised rounded px-3 py-2 text-slate-400 border border-white/[0.07]"
              title={formatAbsoluteDate(project.auto_reingest_last_checked_at)}
            >
              {timeAgo(project.auto_reingest_last_checked_at)}
            </div>
          </div>
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Last Ingested Commit</label>
            <div
              className="text-sm font-mono bg-surface-raised rounded px-3 py-2 text-slate-400 border border-white/[0.07] truncate"
              title={project.last_ingested_commit_sha ?? ""}
            >
              {project.last_ingested_commit_sha?.slice(0, 12) ?? "Not recorded"}
            </div>
          </div>
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Auto Re-Ingestion Error</label>
            <div
              className={`text-sm bg-surface-raised rounded px-3 py-2 border border-white/[0.07] truncate ${
                project.auto_reingest_error ? "text-red-400" : "text-slate-400"
              }`}
              title={project.auto_reingest_error ?? ""}
            >
              {project.auto_reingest_error ?? "None"}
            </div>
          </div>
          <div className="sm:col-span-2">
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Latest Git Update</label>
            <GitUpdateSummary
              gitAction={gitSummary.gitAction}
              gitStrategy={gitSummary.gitStrategy}
              gitConfidence={gitSummary.gitConfidence}
              gitChangedFiles={gitSummary.gitChangedFiles}
              gitDisplayDocs={gitSummary.gitDisplayDocs}
              gitToolCallCount={gitSummary.gitToolCallCount}
              gitRepairAttempts={gitSummary.gitRepairAttempts}
              gitFallbackUsed={gitSummary.gitFallbackUsed}
              gitRequiredFacts={gitSummary.gitRequiredFacts}
              gitStaleClaims={gitSummary.gitStaleClaims}
              gitManualReviewReason={gitSummary.gitManualReviewReason}
              gitReason={gitSummary.gitReason}
              canRepairGitUpdate={gitSummary.canRepairGitUpdate}
              repairLatestGitUpdate={repairLatestGitUpdate}
              projectId={project.id}
            />
          </div>
          <div className="sm:col-span-2">
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-2">GitHub Integration</label>
            <GithubIntegrationPanel
              project={project}
              newToken={newToken}
              onNewTokenChange={onNewTokenChange}
              registerWebhook={registerWebhook}
              updateGithubToken={updateGithubToken}
            />
          </div>
        </>
      )}
    </div>
  );
}
