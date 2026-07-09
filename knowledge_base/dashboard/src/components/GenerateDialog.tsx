import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useGenerate } from "../hooks/useProjects";
import { useModels } from "../hooks/useDiscovery";
import { usePluginPacks } from "../hooks/useProjects";
import type { Project } from "../types";

interface Props {
  project: Project;
  open: boolean;
  onClose: () => void;
  onTriggered?: (projectId: string) => void;
}

type PackChoice =
  | { kind: "project_default" }
  | { kind: "none" }
  | { kind: "plugin"; id: number };

export default function GenerateDialog({ project, open, onClose, onTriggered }: Props) {
  const generate = useGenerate();
  const { data: modelsData } = useModels();
  const { data: pluginPacks } = usePluginPacks();

  const [resume, setResume] = useState(true);
  const [packChoice, setPackChoice] = useState<PackChoice>({ kind: "project_default" });
  const [runInvestigationModel, setRunInvestigationModel] = useState("");
  const [runAnalysisModel, setRunAnalysisModel] = useState("");
  const [runWriteModel, setRunWriteModel] = useState("");

  useEffect(() => {
    if (open) {
      setResume(true);
      setPackChoice({ kind: "project_default" });
      setRunInvestigationModel("");
      setRunAnalysisModel("");
      setRunWriteModel("");
    }
  }, [open, project.id]);

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

  const savedPluginPack = useMemo(
    () =>
      pluginPacks?.find((p) => p.id === project.plugin_pack_id) ?? null,
    [pluginPacks, project.plugin_pack_id],
  );

  if (!open) return null;

  function effectivePluginPackId(): number | null | "project_default" {
    if (packChoice.kind === "plugin") return packChoice.id;
    if (packChoice.kind === "none") return null;
    return "project_default";
  }

  function submit() {
    const choice = effectivePluginPackId();
    const pluginPackId = choice === "project_default" ? null : choice;
    generate.mutate(
      {
        projectId: project.id,
        resume,
        investigationModel: runInvestigationModel || savedInvestigation,
        model: runAnalysisModel || null,
        writeModel: runWriteModel || savedWrite,
        pluginPackId,
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
            Plugin Pack
          </h4>
          <div className="space-y-1.5">
            <PackOption
              checked={packChoice.kind === "project_default"}
              onCheck={() => setPackChoice({ kind: "project_default" })}
              title="Use project default"
              subtitle={
                project.plugin_pack_id
                  ? `Project is assigned to pack #${project.plugin_pack_id}${
                      savedPluginPack ? ` — ${savedPluginPack.name} v${savedPluginPack.version}` : ""
                    }`
                  : "Project has no plugin pack assigned — generation will use the standard harness."
              }
            />
            <PackOption
              checked={packChoice.kind === "none"}
              onCheck={() => setPackChoice({ kind: "none" })}
              title="No plugin pack (force standard harness)"
              subtitle="Ignore the project's assigned pack for this run only."
            />
            {(pluginPacks ?? []).map((pack) => (
              <PackOption
                key={pack.id}
                checked={packChoice.kind === "plugin" && packChoice.id === pack.id}
                onCheck={() => setPackChoice({ kind: "plugin", id: pack.id })}
                title={`${pack.name} v${pack.version}`}
                subtitle={
                  pack.last_commit_sha
                    ? `${pack.repo_url ?? "local"} @ ${pack.last_commit_sha.slice(0, 7)}`
                    : (pack.repo_url ?? "local")
                }
              />
            ))}
            {!pluginPacks?.length && (
              <p className="text-sm text-slate-400 px-1">
                No plugin packs registered. Ask a superadmin to register one on the Packs page.
              </p>
            )}
          </div>
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
            disabled={generate.isPending}
          >
            {generate.isPending ? "Starting…" : "Generate"}
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
