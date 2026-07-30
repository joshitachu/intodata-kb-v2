import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useGenerate, usePackWorkspace } from "../hooks/useProjects";
import { useModels } from "../hooks/useDiscovery";
import type { Project } from "../types";

interface Props {
  project: Project;
  open: boolean;
  onClose: () => void;
  onTriggered?: (projectId: string) => void;
}

export default function GenerateDialog({ project, open, onClose, onTriggered }: Props) {
  const generate = useGenerate();
  const { data: modelsData } = useModels();
  const workspaceQuery = usePackWorkspace(project.id);

  const [resume, setResume] = useState(true);
  const [organizationPackId, setOrganizationPackId] = useState<number | null>(null);
  const [privateVersionIds, setPrivateVersionIds] = useState<number[]>([]);
  const [runInvestigationModel, setRunInvestigationModel] = useState("");
  const [runAnalysisModel, setRunAnalysisModel] = useState("");
  const [runWriteModel, setRunWriteModel] = useState("");
  const [openCodeRoute, setOpenCodeRoute] = useState("");
  const [executionEngine, setExecutionEngine] = useState<
    "legacy" | "opencode" | "opencode_orchestrated" | "opencode_grounded"
  >("legacy");

  useEffect(() => {
    if (open) {
      setResume(true);
      setOrganizationPackId(null);
      setPrivateVersionIds([]);
      setRunInvestigationModel("");
      setRunAnalysisModel("");
      setRunWriteModel("");
      setOpenCodeRoute("");
      setExecutionEngine("legacy");
    }
  }, [open, project.id]);

  useEffect(() => {
    if (workspaceQuery.data?.project_id === project.id) {
      setOrganizationPackId(workspaceQuery.data.selected_organization_pack_id);
      setPrivateVersionIds(workspaceQuery.data.selected_private_version_ids);
    }
  }, [project.id, workspaceQuery.data]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

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
      ? localStorage.getItem(`project_investigation_model_${project.id}`) || null
      : null;
  const savedWrite =
    typeof window !== "undefined"
      ? localStorage.getItem(`project_write_model_${project.id}`) || null
      : null;
  const analysisModels = models.filter((m) => m.supports_analysis);
  const writingModels = models.filter((m) => m.supports_writing);
  const openCodeModels = models.filter(
    (m) => m.available && m.supports_analysis && m.supports_writing,
  );
  const selectedOpenCodeModel =
    openCodeModels.find(
      (m) => `${m.provider}::${m.id}` === openCodeRoute,
    ) ??
    openCodeModels.find((m) => m.is_default_analysis) ??
    openCodeModels[0];

  if (!open) return null;

  function submit() {
    if (
      executionEngine === "legacy" &&
      (
        workspaceQuery.isLoading ||
        workspaceQuery.isError ||
        workspaceQuery.data?.project_id !== project.id
      )
    ) {
      return;
    }
    generate.mutate(
      {
        projectId: project.id,
        resume,
        investigationModel: runInvestigationModel || savedInvestigation,
        model: runAnalysisModel || null,
        writeModel: runWriteModel || savedWrite,
        organizationPackId,
        privateVersionIds,
        executionEngine,
        openCodeProvider: selectedOpenCodeModel?.provider,
        openCodeModel: selectedOpenCodeModel?.id,
      },
      {
        onSuccess: () => {
          onTriggered?.(project.id);
          onClose();
        },
      },
    );
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4"
      onClick={onClose}
    >
      <div
        className="bg-surface-raised border border-white/[0.14] rounded-lg shadow-2xl ring-1 ring-black/50 w-full max-w-2xl max-h-[90vh] flex flex-col animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-7 pt-6 pb-4 border-b border-white/[0.08]">
          <div>
            <h3 className="text-lg font-semibold text-slate-100">Generate Docs</h3>
            <p className="text-xs text-slate-400 mt-1 font-mono">{project.id}</p>
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
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-7 py-5 space-y-6">
        <section className="space-y-3">
          <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
            Execution engine
          </h4>
          <div className="grid gap-2 sm:grid-cols-2">
            <PackOption
              checked={executionEngine === "legacy"}
              onCheck={() => setExecutionEngine("legacy")}
              title="Current documentation workflow"
              subtitle="Uses the existing three-phase models and publishes through the normal workflow."
            />
            <PackOption
              checked={executionEngine === "opencode"}
              onCheck={() => setExecutionEngine("opencode")}
              title="OpenCode single-agent"
              subtitle="One OpenCode agent uses read, glob, grep, edit, and write across the frozen AlitaLand scan and exact legacy prompts/evidence. Isolated; never publishes or enters RAG."
            />
            <PackOption
              checked={executionEngine === "opencode_orchestrated"}
              onCheck={() => setExecutionEngine("opencode_orchestrated")}
              title="OpenCode orchestrated"
              subtitle="A primary agent plans and delegates evidence, analysis, writing, and review to bounded specialist subagents. Isolated; never publishes or enters RAG."
            />
            <PackOption
              checked={executionEngine === "opencode_grounded"}
              onCheck={() => setExecutionEngine("opencode_grounded")}
              title="OpenCode grounded"
              subtitle="Recommended candidate: deterministic scanner facts, seven isolated Nexus-scheduled roles, cross-document review, and bounded fact repair. Isolated; never publishes or enters RAG."
            />
          </div>
        </section>

        {executionEngine === "legacy" ? (
          <>
        <section className="space-y-3">
          <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
            Nexus Documentation Pack
          </h4>
          {workspaceQuery.isLoading && (
            <p className="text-sm text-slate-400">Loading this project's pack selection...</p>
          )}
          {workspaceQuery.error && (
            <p className="text-sm text-amber-400">
              Pack selection could not be loaded. Reload it before starting documentation generation.
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
            Models that will be used
          </h4>
          <ModelRow
            label="Phase 1 — Investigation"
            effective={runInvestigationModel || savedInvestigation || defaultInvestigation?.id || "server config"}
            defaultLabel={
              defaultInvestigation
                ? `${defaultInvestigation.label} (${defaultInvestigation.id})`
                : "server config"
            }
            note={runInvestigationModel ? "One-off override for this run." : savedInvestigation ? "Override saved in local storage." : null}
          />
          <ModelSelect
            value={runInvestigationModel}
            onChange={setRunInvestigationModel}
            models={analysisModels}
            placeholder="Use saved/default investigation model"
          />
          <ModelRow
            label="Phase 2 — Analysis"
            effective={runAnalysisModel || defaultAnalysis?.id || "server config"}
            defaultLabel={
              defaultAnalysis
                ? `${defaultAnalysis.label} (${defaultAnalysis.id})`
                : "server config"
            }
            note={runAnalysisModel ? "One-off override for this run." : project.doc_generation_provider ? `Project provider: ${project.doc_generation_provider}` : null}
          />
          <ModelSelect
            value={runAnalysisModel}
            onChange={setRunAnalysisModel}
            models={analysisModels}
            placeholder="Use project/default analysis model"
          />
          <ModelRow
            label="Phase 3 — Doc Writing"
            effective={runWriteModel || savedWrite || defaultWriting?.id || "server config"}
            defaultLabel={
              defaultWriting
                ? `${defaultWriting.label} (${defaultWriting.id})`
                : "server config"
            }
            note={runWriteModel ? "One-off override for this run." : savedWrite ? "Override saved in local storage." : null}
          />
          <ModelSelect
            value={runWriteModel}
            onChange={setRunWriteModel}
            models={writingModels}
            placeholder="Use saved/default write model"
          />
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
          </>
        ) : (
          <section className="space-y-4 rounded border border-amber-400/30 bg-amber-400/[0.06] px-4 py-3">
            <h4 className="text-sm font-medium text-amber-200">Isolated OpenCode candidate</h4>
            <p className="mt-1 text-xs leading-5 text-slate-300">
              Nexus freezes the current approved IntoDoc scan and, when available, the latest
              completed legacy baseline for comparison. It then asks {executionEngine === "opencode_grounded"
                ? "Nexus to schedule a fact-grounded sequence of isolated OpenCode specialists"
                : executionEngine === "opencode_orchestrated"
                  ? "an OpenCode orchestrator and its bounded specialist subagents"
                  : "one OpenCode agent"} to create the same ten documentation outputs. Results
              and usage appear in Experiments. Existing documentation remains unchanged.
            </p>
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-slate-300">
                OpenCode provider and model
              </span>
              <select
                aria-label="OpenCode provider and model"
                value={
                  selectedOpenCodeModel
                    ? `${selectedOpenCodeModel.provider}::${selectedOpenCodeModel.id}`
                    : ""
                }
                onChange={(event) => setOpenCodeRoute(event.target.value)}
                className="input-dark w-full"
              >
                {openCodeModels.map((model) => (
                  <option
                    key={`${model.provider}:${model.id}`}
                    value={`${model.provider}::${model.id}`}
                  >
                    {model.provider} · {model.label} ({model.id})
                  </option>
                ))}
              </select>
              {openCodeModels.length === 0 && (
                <span className="text-xs text-red-300">
                  No available model supports both analysis and writing.
                </span>
              )}
            </label>
          </section>
        )}

        {generate.isError && (
          <p className="text-sm text-red-400 font-mono break-all">
            {(generate.error as Error | null)?.message ?? "Generation failed to start."}
          </p>
        )}
        </div>

        <div className="flex items-center justify-end gap-2 px-7 py-4 border-t border-white/[0.06]">
          <button
            type="button"
            className="btn-ghost"
            onClick={onClose}
            disabled={generate.isPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={submit}
            disabled={
              generate.isPending ||
              (executionEngine !== "legacy" && !selectedOpenCodeModel) ||
              (executionEngine === "legacy" && (
                workspaceQuery.isLoading ||
                workspaceQuery.isError ||
                workspaceQuery.data?.project_id !== project.id
              ))
            }
          >
            {executionEngine === "legacy" && workspaceQuery.isLoading
              ? "Loading Packs..."
              : generate.isPending
                ? "Starting..."
                : executionEngine === "opencode"
                  ? "Start Single-Agent Candidate"
                  : executionEngine === "opencode_orchestrated"
                    ? "Start Orchestrated Candidate"
                    : executionEngine === "opencode_grounded"
                      ? "Start Grounded Candidate"
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
  models: { id: string; label: string; available: boolean; unavailable_reason: string | null }[];
  placeholder: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="input-dark"
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
        className="mt-1 h-4 w-4 accent-accent-primary"
      />
      <div className="min-w-0">
        <div className="text-sm font-medium text-slate-100">{title}</div>
        <div className="text-xs text-slate-400 mt-1 break-words">{subtitle}</div>
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
        <div className="text-sm font-medium text-accent-primary truncate" title={effective}>
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
