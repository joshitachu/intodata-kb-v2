import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useGenerate, usePackWorkspace } from "../hooks/useProjects";
import { useModels } from "../hooks/useDiscovery";
import { useModalDialog } from "../hooks/useModalDialog";
import type { Project } from "../types";

interface Props {
  project: Project;
  open: boolean;
  onClose: () => void;
  onTriggered?: (projectId: string) => void;
}

type GenerationMethod = "legacy" | "opencode";

export default function GenerateDialog({
  project,
  open,
  onClose,
  onTriggered,
}: Props) {
  const generate = useGenerate();
  const { data: modelsData } = useModels();
  const workspaceQuery = usePackWorkspace(project.id);

  const [resume, setResume] = useState(true);
  const [organizationPackId, setOrganizationPackId] = useState<number | null>(
    null,
  );
  const [privateVersionIds, setPrivateVersionIds] = useState<number[]>([]);
  const [runGenerationMethod, setRunGenerationMethod] =
    useState<GenerationMethod | null>(null);
  const [runInvestigationModel, setRunInvestigationModel] = useState("");
  const [runAnalysisModel, setRunAnalysisModel] = useState("");
  const [runWriteModel, setRunWriteModel] = useState("");

  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setResume(true);
      setOrganizationPackId(null);
      setPrivateVersionIds([]);
      setRunGenerationMethod(null);
      setRunInvestigationModel("");
      setRunAnalysisModel("");
      setRunWriteModel("");
      generate.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, project.id]);

  useEffect(() => {
    if (workspaceQuery.data?.project_id === project.id) {
      setOrganizationPackId(workspaceQuery.data.selected_organization_pack_id);
      setPrivateVersionIds(workspaceQuery.data.selected_private_version_ids);
    }
  }, [project.id, workspaceQuery.data]);

  useModalDialog({ open, onClose, containerRef });

  const models = modelsData?.models ?? [];
  const defaultAnalysis = useMemo(
    () => models.find((m) => m.is_default_analysis),
    [models],
  );
  const defaultInvestigation = useMemo(
    () => models.find((m) => m.is_default_investigation) ?? defaultAnalysis,
    [models, defaultAnalysis],
  );
  const defaultWriting = useMemo(
    () => models.find((m) => m.is_default_writing),
    [models],
  );

  const savedInvestigation =
    typeof window !== "undefined"
      ? localStorage.getItem(`project_investigation_model_${project.id}`) ||
        null
      : null;
  const savedWrite =
    typeof window !== "undefined"
      ? localStorage.getItem(`project_write_model_${project.id}`) || null
      : null;
  const selectableAnalysisModels = models.filter(
    (model) =>
      model.supports_analysis &&
      model.id !== "opencode-cli" &&
      model.id !== "claude-cli",
  );
  const selectableWritingModels = models.filter(
    (model) =>
      model.supports_writing &&
      model.id !== "opencode-cli" &&
      model.id !== "claude-cli",
  );
  const configuredForOpenCode =
    project.doc_generation_provider === "opencode-cli" ||
    savedInvestigation === "opencode-cli" ||
    (!project.doc_generation_provider &&
      (defaultInvestigation?.id === "opencode-cli" ||
        defaultAnalysis?.id === "opencode-cli"));
  const generationMethod =
    runGenerationMethod ?? (configuredForOpenCode ? "opencode" : "legacy");
  const usesOpenCode = generationMethod === "opencode";
  const configuredLegacyAnalysisModel =
    project.doc_generation_provider &&
    project.doc_generation_provider !== "custom" &&
    project.doc_generation_provider !== "opencode-cli" &&
    project.doc_generation_provider !== "claude-cli"
      ? project.doc_generation_provider
      : null;
  const savedLegacyInvestigation =
    savedInvestigation === "opencode-cli" || savedInvestigation === "claude-cli"
      ? null
      : savedInvestigation;

  const packSelectionUnavailable =
    workspaceQuery.isLoading ||
    workspaceQuery.isError ||
    workspaceQuery.data?.project_id !== project.id;

  if (!open) return null;

  function submit() {
    if (packSelectionUnavailable) return;
    generate.mutate(
      {
        projectId: project.id,
        resume,
        investigationModel: usesOpenCode
          ? null
          : runInvestigationModel || savedLegacyInvestigation,
        model: usesOpenCode
          ? runAnalysisModel || null
          : runAnalysisModel || configuredLegacyAnalysisModel,
        writeModel: usesOpenCode ? null : runWriteModel || savedWrite,
        organizationPackId,
        privateVersionIds,
        generationMethod: usesOpenCode ? "opencode-cli" : "custom",
      },
      {
        onSuccess: () => {
          onTriggered?.(project.id);
          onClose();
        },
      },
    );
  }

  const submitError = generate.error as Error | null;
  const submitFailed = generate.isError;
  const submitPending = generate.isPending;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4"
      onClick={onClose}
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="generate-dialog-title"
        className="bg-surface-raised border border-white/[0.14] rounded-lg shadow-2xl ring-1 ring-black/50 w-full max-w-2xl max-h-[90vh] flex flex-col animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-7 pt-6 pb-4 border-b border-white/[0.08]">
          <div>
            <h3
              id="generate-dialog-title"
              className="text-lg font-semibold text-slate-100"
            >
              Generate Docs
            </h3>
            <p className="text-xs text-slate-400 mt-1 font-mono">
              {project.id}
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            title="Close"
            className="shrink-0 -mr-1.5 -mt-1 p-1.5 rounded text-slate-400 hover:text-slate-100 hover:bg-white/[0.08] transition-colors"
            onClick={onClose}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-5 w-5"
              aria-hidden="true"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-7 py-5 space-y-6">
          <section className="space-y-3">
            <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
              Nexus Documentation Pack
            </h4>
            {workspaceQuery.isLoading && (
              <p className="text-sm text-slate-400">
                Loading this project's pack selection...
              </p>
            )}
            {workspaceQuery.error && (
              <p className="text-sm text-amber-400">
                Pack selection could not be loaded. Reload it before starting
                documentation generation.
              </p>
            )}
            {workspaceQuery.data && (
              <div className="space-y-1.5">
                <PackOption
                  checked={organizationPackId === null}
                  onCheck={() => setOrganizationPackId(null)}
                  title="Organization default"
                  subtitle="Use the approved Nexus documentation baseline."
                />
                {workspaceQuery.data.organization_packs.map((pack) => (
                  <PackOption
                    key={pack.id}
                    checked={organizationPackId === pack.id}
                    onCheck={() => setOrganizationPackId(pack.id)}
                    title={`${pack.name} v${pack.version}`}
                    subtitle={pack.description ?? "Organization base pack"}
                  />
                ))}
                {workspaceQuery.data.personal_packs.map((pack) => (
                  <label
                    key={pack.id}
                    className="flex items-start gap-3 px-3.5 py-2.5 rounded border border-white/[0.08] hover:border-white/[0.18] hover:bg-white/[0.04] cursor-pointer transition-all"
                  >
                    <input
                      type="checkbox"
                      checked={privateVersionIds.includes(pack.id)}
                      onChange={() =>
                        setPrivateVersionIds((current) =>
                          current.includes(pack.id)
                            ? current.filter((id) => id !== pack.id)
                            : [...current, pack.id],
                        )
                      }
                      className="mt-1 h-4 w-4 accent-accent-primary"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-slate-100">
                        Add {pack.name} v{pack.version}
                      </span>
                      <span className="mt-1 block text-xs text-slate-400">
                        {pack.description ?? "Personal additive pack"}
                        {pack.output_documents.length > 0
                          ? ` Outputs: ${pack.output_documents.join(", ")}`
                          : ""}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </section>

          <section className="space-y-3">
            <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
              Generation method
            </h4>
            <div
              role="tablist"
              aria-label="Generation method"
              className="grid grid-cols-2 gap-2 rounded border border-white/[0.08] bg-black/10 p-1"
            >
              <button
                type="button"
                role="tab"
                aria-selected={generationMethod === "legacy"}
                onClick={() => setRunGenerationMethod("legacy")}
                className={`rounded px-3 py-2 text-sm font-medium transition-colors ${
                  generationMethod === "legacy"
                    ? "bg-accent-primary text-surface-bg"
                    : "text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
                }`}
              >
                Legacy Pipeline
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={generationMethod === "opencode"}
                onClick={() => setRunGenerationMethod("opencode")}
                className={`rounded px-3 py-2 text-sm font-medium transition-colors ${
                  generationMethod === "opencode"
                    ? "bg-accent-primary text-surface-bg"
                    : "text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
                }`}
              >
                OpenCode
              </button>
            </div>
            <p className="text-xs text-slate-500">
              {usesOpenCode
                ? "One OpenCode orchestrator session runs the complete legacy-compatible workflow."
                : "The existing Python pipeline runs each legacy phase with its configured model."}
            </p>
          </section>

          <section className="space-y-3">
            <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
              {usesOpenCode ? "Session model" : "Models that will be used"}
            </h4>
            {usesOpenCode ? (
              <>
                <ModelRow
                  label="OpenCode Session Model"
                  effective={runAnalysisModel || "Select a model"}
                  defaultLabel="Required for this run"
                  note="One model runs Investigation, Analysis, Documentation, and Topology in the same OpenCode session."
                />
                <ModelSelect
                  value={runAnalysisModel}
                  onChange={setRunAnalysisModel}
                  models={selectableAnalysisModels}
                  placeholder="Select OpenCode session model (required)"
                />
                {!runAnalysisModel && (
                  <p className="text-xs text-amber-400">
                    Choose the actual model OpenCode should use. OpenCode CLI is
                    the runtime, not a model.
                  </p>
                )}
              </>
            ) : (
              <>
                <ModelRow
                  label="Phase 1 — Investigation"
                  effective={
                    runInvestigationModel ||
                    savedLegacyInvestigation ||
                    defaultInvestigation?.id ||
                    "server config"
                  }
                  defaultLabel={
                    defaultInvestigation
                      ? `${defaultInvestigation.label} (${defaultInvestigation.id})`
                      : "server config"
                  }
                  note={
                    runInvestigationModel
                      ? "One-off override for this run."
                      : savedLegacyInvestigation
                        ? "Override saved in local storage."
                        : null
                  }
                />
                <ModelSelect
                  value={runInvestigationModel}
                  onChange={setRunInvestigationModel}
                  models={selectableAnalysisModels}
                  placeholder="Use saved/default investigation model"
                />
                <ModelRow
                  label="Phase 2 — Analysis"
                  effective={
                    runAnalysisModel || defaultAnalysis?.id || "server config"
                  }
                  defaultLabel={
                    defaultAnalysis
                      ? `${defaultAnalysis.label} (${defaultAnalysis.id})`
                      : "server config"
                  }
                  note={
                    runAnalysisModel
                      ? "One-off override for this run."
                      : project.doc_generation_provider
                        ? `Project provider: ${project.doc_generation_provider}`
                        : null
                  }
                />
                <ModelSelect
                  value={runAnalysisModel}
                  onChange={setRunAnalysisModel}
                  models={selectableAnalysisModels}
                  placeholder="Use project/default analysis model"
                />
                <ModelRow
                  label="Phase 3 — Doc Writing"
                  effective={
                    runWriteModel ||
                    savedWrite ||
                    defaultWriting?.id ||
                    "server config"
                  }
                  defaultLabel={
                    defaultWriting
                      ? `${defaultWriting.label} (${defaultWriting.id})`
                      : "server config"
                  }
                  note={
                    runWriteModel
                      ? "One-off override for this run."
                      : savedWrite
                        ? "Override saved in local storage."
                        : null
                  }
                />
                <ModelSelect
                  value={runWriteModel}
                  onChange={setRunWriteModel}
                  models={selectableWritingModels}
                  placeholder="Use saved/default write model"
                />
              </>
            )}
          </section>

          <label className="flex items-center gap-2.5 text-sm text-slate-300 select-none">
            <input
              type="checkbox"
              checked={resume}
              onChange={(e) => setResume(e.target.checked)}
              className="h-4 w-4 accent-accent-primary"
            />
            Resume from staging checkpoint
          </label>

          {submitFailed && (
            <p
              role="alert"
              className="text-sm text-red-400 font-mono break-all"
            >
              {submitError?.message ?? "Generation failed to start."}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-7 py-4 border-t border-white/[0.06]">
          <button
            type="button"
            className="btn-ghost"
            onClick={onClose}
            disabled={submitPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={submit}
            disabled={
              submitPending ||
              packSelectionUnavailable ||
              (usesOpenCode && !runAnalysisModel)
            }
          >
            {workspaceQuery.isLoading
              ? "Loading Packs..."
              : submitPending
                ? "Starting..."
                : "Generate"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function ModelSelect({
  value,
  onChange,
  models,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  models: {
    id: string;
    label: string;
    available: boolean;
    unavailable_reason: string | null;
  }[];
  placeholder: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="input-dark"
      aria-label={placeholder}
    >
      <option value="">{placeholder}</option>
      {models.map((model) => (
        <option key={model.id} value={model.id}>
          {model.label} ({model.id})
          {model.available ? "" : " — credentials not configured"}
        </option>
      ))}
    </select>
  );
}

function PackOption({
  checked,
  onCheck,
  title,
  subtitle,
}: {
  checked: boolean;
  onCheck: () => void;
  title: string;
  subtitle: string;
}) {
  return (
    <label
      className={`flex items-start gap-3 px-3.5 py-2.5 rounded border cursor-pointer transition-all ${
        checked
          ? "border-accent-primary/60 bg-accent-primary/[0.08]"
          : "border-white/[0.08] hover:border-white/[0.18] hover:bg-white/[0.04]"
      }`}
    >
      <input
        type="radio"
        checked={checked}
        onChange={onCheck}
        aria-label={title}
        className="mt-1 h-4 w-4 accent-accent-primary"
      />
      <div className="min-w-0">
        <div className="text-sm font-medium text-slate-100">{title}</div>
        <div className="text-xs text-slate-400 mt-1 break-words">
          {subtitle}
        </div>
      </div>
    </label>
  );
}

function ModelRow({
  label,
  effective,
  defaultLabel,
  note,
}: {
  label: string;
  effective: string;
  defaultLabel: string;
  note: string | null;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5 border-b border-white/[0.05] last:border-b-0">
      <div className="text-sm text-slate-300 shrink-0">{label}</div>
      <div className="text-right min-w-0">
        <div
          className="text-sm font-medium text-accent-primary truncate"
          title={effective}
        >
          {effective}
        </div>
        <div className="text-xs text-slate-500 mt-0.5">
          <span className="text-slate-600">default:</span> {defaultLabel}
        </div>
        {note && (
          <div className="text-xs text-slate-500 mt-0.5 italic">{note}</div>
        )}
      </div>
    </div>
  );
}
