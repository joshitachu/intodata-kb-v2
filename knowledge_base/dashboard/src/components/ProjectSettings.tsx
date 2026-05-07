import { useState, useEffect } from "react";
import { usePlatforms, useModels } from "../hooks/useDiscovery";
import {
  useUpdateProject,
  useProjectUsage,
  useDeleteProject,
  useInstructionPack,
  useRepairLatestGitUpdate,
} from "../hooks/useProjects";
import { formatAbsoluteDate, timeAgo } from "../lib/timeago";
import type { Project } from "../types";

interface Props {
  project: Project | null;
  onDeleted?: () => void;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export default function ProjectSettings({ project, onDeleted }: Props) {
  const updateProject = useUpdateProject();
  const deleteProject = useDeleteProject();
  const repairLatestGitUpdate = useRepairLatestGitUpdate();
  const usage = useProjectUsage(project?.id ?? null);
  const instructionPack = useInstructionPack();
  const { data: platformsData } = usePlatforms();
  const { data: modelsData } = useModels();
  const [displayName, setDisplayName] = useState("");
  const [platform, setPlatform] = useState("");
  const [docProvider, setDocProvider] = useState("");
  const [writeModel, setWriteModel] = useState("");
  const [saved, setSaved] = useState(false);
  const [confirmText, setConfirmText] = useState("");

  useEffect(() => {
    if (project) {
      setDisplayName(project.display_name);
      setPlatform(project.platform);
      setDocProvider(project.doc_generation_provider ?? "");
      setWriteModel(localStorage.getItem(`project_write_model_${project.id}`) ?? "");
      setSaved(false);
      setConfirmText("");
    }
  }, [project?.id]);

  function handleWriteModelChange(value: string) {
    setWriteModel(value);
    if (project) {
      if (value) {
        localStorage.setItem(`project_write_model_${project.id}`, value);
      } else {
        localStorage.removeItem(`project_write_model_${project.id}`);
      }
    }
  }

  if (!project) return null;

  const hasChanges =
    displayName !== project.display_name ||
    platform !== project.platform ||
    docProvider !== (project.doc_generation_provider ?? "");
  const platforms = platformsData?.platforms ?? [];
  const analysisModels = modelsData?.models.filter((m) => m.supports_analysis) ?? [];
  const writingModels = modelsData?.models.filter((m) => m.supports_writing) ?? [];
  const defaultAnalysisModel = analysisModels.find((m) => m.is_default_analysis);
  const defaultWritingModel = writingModels.find((m) => m.is_default_writing);
  const defaultAnalysisLabel = defaultAnalysisModel
    ? `${defaultAnalysisModel.label} (${defaultAnalysisModel.id})`
    : "server config";
  const defaultWritingLabel = defaultWritingModel
    ? `${defaultWritingModel.label} (${defaultWritingModel.id})`
    : "server config";
  const hasKnownDocProvider =
    docProvider === "" || analysisModels.some(({ id }) => id === docProvider);
  const hasKnownWriteModel =
    writeModel === "" || writingModels.some(({ id }) => id === writeModel);
  const gitSummary = project.git_update_summary ?? null;
  const gitAction = typeof gitSummary?.action === "string" ? gitSummary.action : null;
  const gitChangedFiles = Array.isArray(gitSummary?.changed_files)
    ? gitSummary.changed_files.filter((item): item is string => typeof item === "string")
    : [];
  const gitPatchedDocs = Array.isArray(gitSummary?.patched_docs)
    ? gitSummary.patched_docs.filter((item): item is string => typeof item === "string")
    : [];
  const gitReason = typeof gitSummary?.reason === "string" ? gitSummary.reason : null;
  const canRepairGitUpdate =
    project.source_type === "git" &&
    typeof gitSummary?.before === "string" &&
    typeof gitSummary?.after === "string" &&
    gitAction !== null;

  function handleSave() {
    if (!project || !hasChanges) return;
    updateProject.mutate(
      {
        projectId: project.id,
        data: {
          display_name: displayName !== project.display_name ? displayName : undefined,
          platform: platform !== project.platform ? platform : undefined,
          doc_generation_provider:
            docProvider !== (project.doc_generation_provider ?? "")
              ? docProvider || null
              : undefined,
        },
      },
      {
        onSuccess: () => {
          setSaved(true);
          setTimeout(() => setSaved(false), 2000);
        },
      }
    );
  }

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
        <h3 className="text-sm font-mono text-slate-400">Project Settings</h3>
      </div>

      <div className="p-4 space-y-4">
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
                <div className="text-sm bg-surface-raised rounded px-3 py-2 text-slate-400 border border-white/[0.07]">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                    <span>Action: <span className="font-mono text-slate-300">{gitAction ?? "None"}</span></span>
                    <span>Changed files: <span className="font-mono text-slate-300">{gitChangedFiles.length}</span></span>
                    <span>Patched docs: <span className="font-mono text-slate-300">{gitPatchedDocs.length}</span></span>
                    {canRepairGitUpdate && (
                      <button
                        type="button"
                        className="ml-auto btn-dark text-xs px-2 py-1"
                        disabled={repairLatestGitUpdate.isPending}
                        onClick={() => repairLatestGitUpdate.mutate(project.id)}
                      >
                        {repairLatestGitUpdate.isPending ? "Repairing..." : "Repair Latest"}
                      </button>
                    )}
                  </div>
                  {gitPatchedDocs.length > 0 && (
                    <div className="mt-1 font-mono text-xs text-slate-500 truncate" title={gitPatchedDocs.join(", ")}>
                      {gitPatchedDocs.join(", ")}
                    </div>
                  )}
                  {gitReason && (
                    <div className="mt-1 text-xs text-amber-400 truncate" title={gitReason}>
                      {gitReason}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        <hr className="border-white/[0.05]" />

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Display Name</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input-dark"
            />
          </div>
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Platform</label>
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="input-dark"
            >
              {platforms.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
              {platform && !platforms.some((p) => p.id === platform) && (
                <option value={platform}>{platform}</option>
              )}
            </select>
          </div>
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">
              Doc Generation Provider
              <span className="ml-1 text-slate-700 normal-case tracking-normal">(Phase 2 — Analysis)</span>
            </label>
            <select
              value={docProvider}
              onChange={(e) => setDocProvider(e.target.value)}
              className="input-dark"
            >
              <option value="">Use server default -- {defaultAnalysisLabel}</option>
              {analysisModels.map(({ id, label }) => (
                <option key={id} value={id}>{label}</option>
              ))}
              {!hasKnownDocProvider && (
                <option value={docProvider}>Unsupported saved value — {docProvider}</option>
              )}
            </select>
            {docProvider === "" && (
              <p className="mt-1 text-xs text-slate-600">
                Phase 2 analysis uses the server default: <span className="font-mono text-accent-primary/60">{defaultAnalysisModel?.id ?? "server config"}</span>.
              </p>
            )}
            <p className="mt-1 text-xs text-slate-600">
              Save changes before generating; the Generate button uses the saved provider for Phase 2.
            </p>
            {!hasKnownDocProvider && (
              <p className="mt-1 text-xs text-amber-400">
                This saved provider is from the old routing. Choose a supported analysis model or clear it.
              </p>
            )}
          </div>
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">
              Write Model
              <span className="ml-1 text-slate-700 normal-case tracking-normal">(Phase 3 — Doc Writing)</span>
            </label>
            <select
              value={writeModel}
              onChange={(e) => handleWriteModelChange(e.target.value)}
              className="input-dark"
            >
              <option value="">Use server default -- {defaultWritingLabel}</option>
              {writingModels.map(({ id, label }) => (
                <option key={id} value={id}>{label}</option>
              ))}
              {!hasKnownWriteModel && (
                <option value={writeModel}>Unsupported saved value — {writeModel}</option>
              )}
            </select>
            {writeModel === "" ? (
              <p className="mt-1 text-xs text-slate-600">
                Phase 3 doc writing uses the server default: <span className="font-mono text-accent-primary/60">{defaultWritingModel?.id ?? "server config"}</span>.
              </p>
            ) : (
              <p className="mt-1 text-xs text-slate-600">
                Saved locally. Phase 3 doc-writing uses <span className="font-mono text-accent-primary/60">{writeModel.split("/")[1] ?? writeModel}</span>.
              </p>
            )}
            {!hasKnownWriteModel && (
              <p className="mt-1 text-xs text-amber-400">
                This local write model is not supported by the custom harness. Choose a supported model or clear it.
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={!hasChanges || updateProject.isPending}
            className={`px-4 py-2 text-sm rounded font-medium transition-all ${
              hasChanges
                ? "bg-accent-primary text-surface-bg hover:bg-accent-primary/90"
                : "bg-white/[0.05] text-slate-600 cursor-not-allowed"
            }`}
          >
            {updateProject.isPending ? "Saving..." : "Save Changes"}
          </button>
          {saved && (
            <span className="text-sm text-[#a0e06f] font-medium">Saved!</span>
          )}
          {updateProject.isError && (
            <span className="text-sm text-red-400">
              Failed: {updateProject.error?.message}
            </span>
          )}
        </div>

        {/* Usage Summary */}
        <hr className="border-white/[0.05]" />
        <div>
          <h4 className="font-mono uppercase tracking-wider text-xs text-slate-600 mb-3">Instruction Pack</h4>
          {instructionPack.isLoading ? (
            <p className="text-xs text-slate-500">Loading instruction pack metadata...</p>
          ) : instructionPack.isError ? (
            <p className="text-xs text-red-400">Failed to load instruction pack metadata</p>
          ) : instructionPack.data ? (
            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
                  <div className="text-sm font-mono text-slate-300 truncate" title={instructionPack.data.version}>
                    {instructionPack.data.version}
                  </div>
                  <div className="text-xs text-slate-500">Version</div>
                </div>
                <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07] sm:col-span-2">
                  <div className="text-sm font-mono text-slate-300 truncate" title={instructionPack.data.path}>
                    {instructionPack.data.relative_path}
                  </div>
                  <div className="text-xs text-slate-500">
                    {instructionPack.data.supported ? "Local pack loaded" : "Pack unavailable"}
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {instructionPack.data.preambles.length === 0 ? (
                  <span className="text-xs text-slate-600">No preambles found.</span>
                ) : (
                  instructionPack.data.preambles.map((preamble) => (
                    <span
                      key={preamble.key}
                      className="px-2 py-1 rounded bg-white/[0.04] border border-white/[0.06] text-xs font-mono text-slate-500"
                    >
                      {preamble.key} ({preamble.chars} chars)
                    </span>
                  ))
                )}
              </div>
            </div>
          ) : null}
        </div>

        <hr className="border-white/[0.05]" />
        <div>
          <h4 className="font-mono uppercase tracking-wider text-xs text-slate-600 mb-3">Usage Summary</h4>
          {usage.isLoading ? (
            <p className="text-xs text-slate-500">Loading usage data...</p>
          ) : usage.isError ? (
            <p className="text-xs text-red-400">Failed to load usage data</p>
          ) : usage.data ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
                <div className="text-lg font-mono font-medium text-slate-300">
                  {usage.data.completed_generate_runs}/{usage.data.total_generate_runs}
                </div>
                <div className="text-xs text-slate-500">Generate Runs</div>
              </div>
              <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
                <div className="text-lg font-mono font-medium text-slate-300">
                  {usage.data.completed_scan_runs}/{usage.data.total_scan_runs}
                </div>
                <div className="text-xs text-slate-500">Scan Runs</div>
              </div>
              <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
                <div className="text-lg font-mono font-medium text-accent-primary">
                  {formatTokens(usage.data.total_input_tokens + usage.data.total_output_tokens)}
                </div>
                <div className="text-xs text-slate-500">
                  Total Tokens
                  <span className="block text-slate-600">
                    {formatTokens(usage.data.total_input_tokens)} in / {formatTokens(usage.data.total_output_tokens)} out
                  </span>
                </div>
              </div>
              <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
                <div className="text-lg font-mono font-medium text-[#a0e06f]">
                  ${usage.data.total_cost_usd.toFixed(2)}
                </div>
                <div className="text-xs text-slate-500">Total Cost</div>
              </div>
            </div>
          ) : null}
        </div>

        {/* Danger Zone */}
        <hr className="border-white/[0.05]" />
        <div className="border border-red-500/30 rounded-lg p-4 space-y-3">
          <h4 className="font-mono uppercase tracking-wider text-xs text-red-400">Danger Zone</h4>
          <p className="text-sm text-slate-400">
            Permanently delete this project and all associated data including scan results,
            generated docs, and RAG documents. This action cannot be undone.
          </p>
          <div className="flex flex-col sm:flex-row items-start sm:items-end gap-3">
            <div className="flex-1 w-full sm:w-auto">
              <label className="block text-xs text-slate-500 mb-1">
                Type <span className="font-mono text-red-400">{project.id}</span> to confirm
              </label>
              <input
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={project.id}
                className="input-dark w-full sm:w-64"
                disabled={deleteProject.isPending}
              />
            </div>
            <button
              onClick={() => {
                if (!project || confirmText !== project.id) return;
                deleteProject.mutate(project.id, {
                  onSuccess: () => {
                    setConfirmText("");
                    onDeleted?.();
                  },
                });
              }}
              disabled={confirmText !== project.id || deleteProject.isPending}
              className={`px-4 py-2 text-sm rounded font-medium transition-all whitespace-nowrap ${
                confirmText === project.id && !deleteProject.isPending
                  ? "bg-red-600 text-white hover:bg-red-500"
                  : "bg-white/[0.05] text-slate-600 cursor-not-allowed"
              }`}
            >
              {deleteProject.isPending ? "Deleting..." : "Permanently Delete"}
            </button>
          </div>
          {deleteProject.isError && (
            <p className="text-sm text-red-400">
              Failed: {deleteProject.error?.message}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
