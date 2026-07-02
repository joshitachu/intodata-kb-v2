import { useEffect, useMemo, useState } from "react";
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

  useEffect(() => {
    if (open) {
      setResume(true);
      setPackChoice({ kind: "project_default" });
    }
  }, [open, project.id]);

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
        investigationModel: savedInvestigation,
        model: null,
        writeModel: savedWrite,
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md">
      <div className="bg-surface-card border border-white/[0.07] rounded shadow-2xl w-full max-w-lg p-6 space-y-5 animate-fade-in">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-medium text-slate-200">Generate Docs</h3>
            <p className="text-xs text-slate-500 mt-1 font-mono">{project.id}</p>
          </div>
          <button
            type="button"
            className="text-slate-500 hover:text-slate-300 text-xs"
            onClick={onClose}
          >
            Close
          </button>
        </div>

        <section className="space-y-2">
          <h4 className="font-mono uppercase tracking-wider text-[10px] text-slate-600">
            Pack
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
              <p className="text-xs text-slate-600 px-1">
                No plugin packs registered. Ask a superadmin to register one on the Packs page.
              </p>
            )}
          </div>
        </section>

        <section className="space-y-2">
          <h4 className="font-mono uppercase tracking-wider text-[10px] text-slate-600">
            Models that will be used
          </h4>
          <ModelRow
            label="Phase 1 — Investigation"
            effective={savedInvestigation ?? defaultInvestigation?.id ?? "server config"}
            defaultLabel={
              defaultInvestigation
                ? `${defaultInvestigation.label} (${defaultInvestigation.id})`
                : "server config"
            }
            note={savedInvestigation ? "Override saved in local storage." : null}
          />
          <ModelRow
            label="Phase 2 — Analysis"
            effective={defaultAnalysis?.id ?? "server config"}
            defaultLabel={
              defaultAnalysis
                ? `${defaultAnalysis.label} (${defaultAnalysis.id})`
                : "server config"
            }
            note={project.doc_generation_provider ? `Project provider: ${project.doc_generation_provider}` : null}
          />
          <ModelRow
            label="Phase 3 — Doc Writing"
            effective={savedWrite ?? defaultWriting?.id ?? "server config"}
            defaultLabel={
              defaultWriting
                ? `${defaultWriting.label} (${defaultWriting.id})`
                : "server config"
            }
            note={savedWrite ? "Override saved in local storage." : null}
          />
        </section>

        <label className="flex items-center gap-2 text-xs text-slate-400 select-none">
          <input
            type="checkbox"
            checked={resume}
            onChange={(e) => setResume(e.target.checked)}
            className="h-3 w-3 accent-accent-primary"
          />
          Resume from staging checkpoint
        </label>

        {generate.isError && (
          <p className="text-xs text-red-400 font-mono break-all">
            {(generate.error as Error | null)?.message ?? "Generation failed to start."}
          </p>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
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
    </div>
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
      className={`flex items-start gap-2.5 px-3 py-2 rounded border cursor-pointer transition-all ${
        checked
          ? "border-accent-primary/50 bg-accent-primary/[0.06]"
          : "border-white/[0.06] hover:border-white/[0.12] hover:bg-white/[0.03]"
      }`}
    >
      <input
        type="radio"
        checked={checked}
        onChange={onCheck}
        className="mt-0.5 h-3 w-3 accent-accent-primary"
      />
      <div className="min-w-0">
        <div className="text-sm text-slate-200">{title}</div>
        <div className="text-xs text-slate-500 mt-0.5 break-all">{subtitle}</div>
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
    <div className="flex items-start justify-between gap-3 text-xs">
      <div className="font-mono text-slate-500 shrink-0">{label}</div>
      <div className="text-right min-w-0">
        <div className="font-mono text-accent-primary/70 truncate" title={effective}>
          {effective}
        </div>
        <div className="text-slate-600 mt-0.5">default: {defaultLabel}</div>
        {note && <div className="text-slate-600 mt-0.5">{note}</div>}
      </div>
    </div>
  );
}