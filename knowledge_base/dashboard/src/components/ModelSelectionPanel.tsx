import type { UseMutationResult } from "@tanstack/react-query";
import type { ModelDescriptor, PluginPack, Project } from "../types";

interface Props {
  project: Project;
  docProvider: string;
  onDocProviderChange: (value: string) => void;
  investigationModel: string;
  onInvestigationModelChange: (value: string) => void;
  writeModel: string;
  onWriteModelChange: (value: string) => void;
  packDir: string;
  onPackDirChange: (value: string) => void;
  analysisModels: ModelDescriptor[];
  writingModels: ModelDescriptor[];
  defaultAnalysisLabel: string;
  defaultInvestigationLabel: string;
  defaultWritingLabel: string;
  defaultAnalysisModelId: string | undefined;
  defaultWritingModelId: string | undefined;
  hasKnownDocProvider: boolean;
  hasKnownInvestigationModel: boolean;
  hasKnownWriteModel: boolean;
  instructionPackVersion: string | undefined;
  pluginPacks: PluginPack[] | undefined;
  assignPluginPack: UseMutationResult<unknown, Error, { projectId: string; pluginPackId: number | null }, unknown>;
}

export default function ModelSelectionPanel({
  project,
  docProvider,
  onDocProviderChange,
  investigationModel,
  onInvestigationModelChange,
  writeModel,
  onWriteModelChange,
  packDir,
  onPackDirChange,
  analysisModels,
  writingModels,
  defaultAnalysisLabel,
  defaultInvestigationLabel,
  defaultWritingLabel,
  defaultAnalysisModelId,
  defaultWritingModelId,
  hasKnownDocProvider,
  hasKnownInvestigationModel,
  hasKnownWriteModel,
  instructionPackVersion,
  pluginPacks,
  assignPluginPack,
}: Props) {
  return (
    <>
      <div>
        <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">
          Doc Generation Provider
          <span className="ml-1 text-slate-700 normal-case tracking-normal">(Phase 2 — Analysis)</span>
        </label>
        <select
          value={docProvider}
          onChange={(e) => onDocProviderChange(e.target.value)}
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
            Phase 2 analysis uses the server default: <span className="font-mono text-accent-primary/60">{defaultAnalysisModelId ?? "server config"}</span>.
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
          Investigation Model
          <span className="ml-1 text-slate-700 normal-case tracking-normal">(Phase 1 — Evidence)</span>
        </label>
        <select
          value={investigationModel}
          onChange={(e) => onInvestigationModelChange(e.target.value)}
          className="input-dark"
        >
          <option value="">Use server default -- {defaultInvestigationLabel}</option>
          {analysisModels.map(({ id, label }) => (
            <option key={id} value={id}>{label}</option>
          ))}
          {!hasKnownInvestigationModel && (
            <option value={investigationModel}>Unsupported saved value — {investigationModel}</option>
          )}
        </select>
        <p className="mt-1 text-xs text-slate-600">
          The Generate button uses this model for Phase 1 chunk investigation.
        </p>
        {!hasKnownInvestigationModel && (
          <p className="mt-1 text-xs text-amber-400">
            This saved model is not in the current model registry. Choose a supported model or clear it.
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
          onChange={(e) => onWriteModelChange(e.target.value)}
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
            Phase 3 doc writing uses the server default: <span className="font-mono text-accent-primary/60">{defaultWritingModelId ?? "server config"}</span>.
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
      <div className="sm:col-span-2">
        <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">
          Instruction Pack Dir
        </label>
        <input
          type="text"
          value={packDir}
          onChange={(e) => onPackDirChange(e.target.value)}
          placeholder="packs/default"
          className="input-dark"
        />
        <p className="mt-1 text-xs text-slate-600">
          Override the instruction pack for this project. Leave blank to use the server default
          {instructionPackVersion ? ` (${instructionPackVersion})` : ""}.
        </p>
      </div>
      <div className="sm:col-span-2">
        <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">
          Plugin Pack
        </label>
        <select
          className="input-dark"
          value={project.plugin_pack_id ?? ""}
          onChange={(e) => {
            const val = e.target.value;
            assignPluginPack.mutate({
              projectId: project.id,
              pluginPackId: val === "" ? null : Number(val),
            });
          }}
          disabled={assignPluginPack.isPending}
        >
          <option value="">— none (use default harness) —</option>
          {(pluginPacks ?? []).map((pack) => (
            <option key={pack.id} value={pack.id}>
              {pack.name} v{pack.version}
              {pack.last_commit_sha ? ` (${pack.last_commit_sha.slice(0, 7)})` : ""}
            </option>
          ))}
        </select>
        {project.plugin_pack_id != null && (() => {
          const selected = (pluginPacks ?? []).find((p) => p.id === project.plugin_pack_id);
          return selected ? (
            <p className="mt-1 text-xs text-slate-600">
              {selected.repo_url
                ? <>Auto-updates from <span className="font-mono">{selected.repo_url}</span></>
                : "Local pack — no auto-update"}
              {selected.last_commit_sha && (
                <> · SHA <span className="font-mono">{selected.last_commit_sha.slice(0, 7)}</span></>
              )}
            </p>
          ) : null;
        })()}
      </div>
    </>
  );
}
