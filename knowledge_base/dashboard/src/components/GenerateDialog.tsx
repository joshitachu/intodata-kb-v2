import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useAssignPluginPack, useGenerate, usePluginPacks } from "../hooks/useProjects";
import { useModels } from "../hooks/useDiscovery";
import { useModalDialog } from "../hooks/useModalDialog";
import {
  useConnectorCatalog,
  useConnectors,
  useProjectConnectorBindings,
} from "../hooks/useConnectors";
import { useRuntimeReadScope, useRuntimeSources } from "../hooks/useRuntimeSources";
import {
  describeEngineReach,
  describeKind,
  engineReadsKind,
  formatSyncedAt,
  isCliSessionEngine,
  type GenerationEngine,
} from "../lib/connectors";
import { api } from "../api/client";
import type {
  ConnectorInstance,
  ConnectorProvider,
  GenerationPlan,
  GenerationPlanPartition,
  Project,
  ProjectConnectorBinding,
} from "../types";

interface Props {
  project: Project;
  open: boolean;
  onClose: () => void;
  onTriggered?: (projectId: string) => void;
  onManageRuntimeEvidence?: (projectId: string) => void;
}

// Same two values as the engine the live-data panel reasons about; kept as one
// type so a third engine cannot be added on one side only.
type GenerationMethod = GenerationEngine;

/**
 * Aliases the Claude Code CLI accepts for `--model`. Deliberately not sourced
 * from the model catalogue: that catalogue lists network routes for the Python
 * harness (`openai/...`, `ollama/...`), none of which this CLI can resolve.
 */
const CLAUDE_MODEL_ALIASES = [
  { id: "sonnet", label: "Sonnet (default)" },
  { id: "opus", label: "Opus" },
  { id: "haiku", label: "Haiku" },
] as const;

/**
 * The generation plan is advisory. It NEVER gates the run.
 *
 * A four-state machine rather than `plan | null` because "not read yet",
 * "unreadable" and "read, nothing to preview" have to look different: collapsing
 * them would either hide a real failure or imply a cheap run that was never
 * confirmed.
 */
type PlanState =
  | { status: "loading" }
  | { status: "ready"; plan: GenerationPlan }
  | { status: "failed"; message: string };

export default function GenerateDialog({
  project,
  open,
  onClose,
  onTriggered,
  onManageRuntimeEvidence,
}: Props) {
  const generate = useGenerate();
  const { data: modelsData } = useModels();
  const pluginPacksQuery = usePluginPacks();
  const assignPluginPack = useAssignPluginPack();

  const [resume, setResume] = useState(true);
  // Gates the mirrored runtime lane only. Checked by default because it can
  // only ever narrow what the project already granted.
  const [liveEvidence, setLiveEvidence] = useState(true);
  const [pluginPackId, setPluginPackId] = useState<number | null>(project.plugin_pack_id);
  const [runGenerationMethod, setRunGenerationMethod] =
    useState<GenerationMethod | null>(null);
  const [runInvestigationModel, setRunInvestigationModel] = useState("");
  const [runAnalysisModel, setRunAnalysisModel] = useState("");
  const [runWriteModel, setRunWriteModel] = useState("");
  const [planState, setPlanState] = useState<PlanState>({ status: "loading" });
  // Bumped by Retry. Re-requesting on the same key would not re-run the effect.
  const [planReload, setPlanReload] = useState(0);

  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setResume(true);
      setLiveEvidence(true);
      setPluginPackId(project.plugin_pack_id);
      setRunGenerationMethod(null);
      setRunInvestigationModel("");
      setRunAnalysisModel("");
      setRunWriteModel("");
      generate.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, project.id]);

  // Read when the dialog opens, so the split is on screen before anyone commits
  // to the run. The whole call is inside the try: a client that does not have
  // this endpoint yet throws synchronously, and an unhandled throw here would
  // take the dialog down over a preview.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPlanState({ status: "loading" });
    void (async () => {
      try {
        const plan = await api.getGenerationPlan(project.id);
        if (!cancelled) setPlanState({ status: "ready", plan });
      } catch (err) {
        if (!cancelled) {
          setPlanState({
            status: "failed",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, project.id, planReload]);

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
      model.id !== "claude-cli" &&
      model.provider !== "opencode-go",
  );
  const openCodeSessionModels = models.filter(
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
  const configuredForClaude =
    project.doc_generation_provider === "claude-cli" ||
    savedInvestigation === "claude-cli" ||
    (!project.doc_generation_provider &&
      (defaultInvestigation?.id === "claude-cli" ||
        defaultAnalysis?.id === "claude-cli"));
  const defaultGenerationMethod: GenerationMethod = configuredForClaude
    ? "claude"
    : configuredForOpenCode
      ? "opencode"
      : "legacy";
  const generationMethod = runGenerationMethod ?? defaultGenerationMethod;
  const usesOpenCode = generationMethod === "opencode";
  const usesClaude = generationMethod === "claude";
  // Claude Code and OpenCode are one lane with two transports: the same prompt
  // on the same staging, driven by one session model. Everything the dialog
  // does differently for OpenCode it must also do for Claude, so the UI keys on
  // the lane rather than on the transport.
  const usesCliSession = isCliSessionEngine(generationMethod);
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

  const selectedPluginPack = (pluginPacksQuery.data ?? []).find(
    (pack) => pack.id === pluginPackId,
  );
  const selectablePluginPacks = (pluginPacksQuery.data ?? []).filter(
    (pack) => pack.usable || pack.id === pluginPackId,
  );

  // A plan is previewable only for a composite project that has been scanned.
  // A single-run project has no sub-projects to compare and an unscanned one has
  // nothing to compare against; in both cases the dialog behaves as it did
  // before this preview existed.
  const plan = planState.status === "ready" ? planState.plan : null;
  const previewablePlan = !usesCliSession && plan && plan.composite && plan.scan_available ? plan : null;
  // The server's own count, not the length of the list: it owns the skip
  // decision, and its arithmetic is what the run will actually charge for.
  const subprojectsToGenerate = previewablePlan?.will_generate ?? null;

  if (!open) return null;

  function submit() {
    generate.mutate(
      {
        projectId: project.id,
        resume,
        investigationModel: usesCliSession
          ? null
          : runInvestigationModel || savedLegacyInvestigation,
        model: usesCliSession
          ? runAnalysisModel || null
          : runAnalysisModel || configuredLegacyAnalysisModel,
        writeModel: usesCliSession ? null : runWriteModel || savedWrite,
        generationMethod: usesClaude
          ? "claude-cli"
          : usesOpenCode
            ? "opencode-cli"
            : "custom",
        // Preserve the shared request shape across generation transports.
        liveEvidence,
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
        className="bg-surface-raised border border-white/[0.14] rounded-lg shadow-2xl ring-1 ring-black/50 w-full max-w-4xl max-h-[90vh] flex flex-col animate-fade-in"
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
          {usesCliSession ? (
            <section className="rounded border border-accent-primary/25 bg-accent-primary/5 p-4">
              <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
                Whole-estate generation
              </h4>
              <p className="mt-2 text-sm text-slate-300">
                {usesClaude ? "Claude Code" : "OpenCode"} receives the complete scanned estate in
                one workspace and runs one autonomous documentation session. Nexus will not split
                this run into sub-projects.
              </p>
            </section>
          ) : (
            <GenerationPlanSection
              state={planState}
              plan={previewablePlan}
              onRetry={() => setPlanReload((n) => n + 1)}
            />
          )}

          <section className="space-y-3 rounded border border-white/[0.08] bg-black/10 p-4">
            <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
              Plugin pack
            </h4>
            {pluginPacksQuery.isLoading && (
              <p className="text-sm text-slate-400">
                Loading available plugin packs...
              </p>
            )}
            {pluginPacksQuery.error && (
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-amber-400">
                <p>
                  Available packs could not be loaded. The saved project pack will still be used.
                </p>
                <button type="button" className="btn-ghost" onClick={() => void pluginPacksQuery.refetch()}>
                  Retry
                </button>
              </div>
            )}
            <label className="block">
              <span className="mb-1.5 block text-xs text-slate-400">Project pack</span>
              <select
                className="input-dark"
                value={pluginPackId ?? ""}
                aria-label="Plugin pack"
                disabled={assignPluginPack.isPending}
                onChange={(event) => {
                  const previousPackId = pluginPackId;
                  const nextPackId = event.target.value ? Number(event.target.value) : null;
                  setPluginPackId(nextPackId);
                  assignPluginPack.mutate(
                    { projectId: project.id, pluginPackId: nextPackId },
                    { onError: () => setPluginPackId(previousPackId) },
                  );
                }}
              >
                <option value="">— none (use the built-in document set) —</option>
                {selectablePluginPacks.map((pack) => (
                  <option key={pack.id} value={pack.id} disabled={!pack.usable}>
                    {pack.name} v{pack.version}
                    {!pack.usable ? " (currently unavailable)" : ""}
                  </option>
                ))}
              </select>
              <span className="mt-1.5 block text-xs text-slate-500">
                {selectedPluginPack?.description
                  ?? (pluginPackId != null
                    ? `Saved plugin pack #${pluginPackId} will be used for this run.`
                    : "Choose a pack to define this project's generated documents.")}
              </span>
              {selectedPluginPack && !selectedPluginPack.usable && (
                <span className="mt-1.5 block text-xs text-amber-400">
                  This previously selected pack is unavailable: {selectedPluginPack.blocked_reason ?? "select another usable pack before generating."}
                </span>
              )}
            </label>
            {assignPluginPack.error && (
              <p role="alert" className="text-sm text-red-400">
                The plugin pack could not be saved. Please try again.
              </p>
            )}
          </section>

          <section className="space-y-3">
            <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
              Generation method
            </h4>
            <div
              role="tablist"
              aria-label="Generation method"
              className="grid grid-cols-3 gap-2 rounded border border-white/[0.08] bg-black/10 p-1"
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
                aria-selected={generationMethod === "claude"}
                onClick={() => setRunGenerationMethod("claude")}
                className={`rounded px-3 py-2 text-sm font-medium transition-colors ${
                  generationMethod === "claude"
                    ? "bg-accent-primary text-surface-bg"
                    : "text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
                }`}
              >
                Claude Code
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
              {usesClaude
                ? "One Claude Code session runs the four-phase workflow. This is the method the prompt is written for; OpenCode is a transport for the same prompt."
                : usesOpenCode
                  ? "One OpenCode session runs the same complete Claude-compatible workflow."
                  : "The existing Python pipeline runs each legacy phase with its configured model."}
            </p>
          </section>

          <section className="space-y-3">
            <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
              {usesCliSession ? "Session model" : "Models that will be used"}
            </h4>
            {usesClaude ? (
              <>
                <ModelRow
                  label="Claude Code Session Model"
                  effective={runAnalysisModel || "Server default (MCP_CLAUDE_MODEL)"}
                  defaultLabel="Optional for this run"
                  note="One model runs Investigation, Analysis, Documentation, and Topology in the same Claude Code session."
                />
                {/*
                  A plain alias list, not the provider catalogue: this value
                  becomes `claude --model <value>`, so an `openai/...` or
                  `ollama/...` route from that catalogue would be rejected by the
                  CLI. Blank is valid and means the server's MCP_CLAUDE_MODEL.
                */}
                <select
                  value={runAnalysisModel}
                  onChange={(event) => setRunAnalysisModel(event.target.value)}
                  className="w-full rounded border border-white/[0.08] bg-black/20 px-3 py-2 text-sm text-slate-200"
                >
                  <option value="">Server default (MCP_CLAUDE_MODEL)</option>
                  {CLAUDE_MODEL_ALIASES.map((alias) => (
                    <option key={alias.id} value={alias.id}>
                      {alias.label}
                    </option>
                  ))}
                </select>
              </>
            ) : usesOpenCode ? (
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
                  models={openCodeSessionModels}
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

          {/* ONE list of every live-data source this run may use, whatever the
              provider. Each row's behaviour is derived from the manifest
              `kind`, never from a provider id -- see `lib/connectors.ts` for
              why the two lanes differ and who reads which. */}
          <LiveDataSources
            projectId={project.id}
            engine={generationMethod}
            liveEvidence={liveEvidence}
            onLiveEvidenceChange={setLiveEvidence}
            onManage={onManageRuntimeEvidence ? () => {
              onClose();
              onManageRuntimeEvidence(project.id);
            } : undefined}
          />

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
              assignPluginPack.isPending ||
              (usesOpenCode && !runAnalysisModel)
            }
          >
            {submitPending
              ? "Starting..."
              : subprojectsToGenerate == null
                ? "Generate"
                : subprojectsToGenerate === 0
                  ? "Generate (nothing to regenerate)"
                  : `Generate ${subprojectsToGenerate} sub-project${subprojectsToGenerate === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function formatGeneratedAt(value: string | null): string {
  if (!value) return "never generated";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "never generated" : date.toLocaleString();
}

/**
 * What the next run will regenerate, what it will reuse, and why.
 *
 * The point of this block is cost honesty: a composite project generates one
 * scoped run per sub-project, and the orchestrator skips any sub-project whose
 * scan fingerprint is unchanged and whose docs are published. Without the
 * preview, "Generate" on a six-sub-project project is a button whose price is
 * somewhere between one sub-project and six, and the operator cannot tell which
 * until the bill arrives.
 *
 * Three rules it must not break:
 *   - it NEVER blocks the run. An unreadable plan is an inline notice, not a
 *     disabled button: the plan is a description of what the orchestrator will
 *     do, and the orchestrator does not need it to run.
 *   - it renders only what the server returned. No local cost model, no
 *     estimated token counts, no "approximately" -- the skip decision lives in
 *     one place and this is a read of it.
 *   - it states the reason CODE per sub-project. This is the operator console,
 *     and `no_published_docs` vs `changed` is the difference between a bug and
 *     an expected charge.
 */
function GenerationPlanSection({
  state,
  plan,
  onRetry,
}: {
  state: PlanState;
  /** The plan when it is previewable at all; null hides the block. */
  plan: GenerationPlan | null;
  onRetry: () => void;
}) {
  if (state.status === "loading") {
    return (
      <p className="text-sm text-slate-500">
        Checking which sub-projects need to regenerate…
      </p>
    );
  }

  if (state.status === "failed") {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-amber-400/30 bg-amber-400/5 p-3">
        <p className="text-sm text-amber-400">
          The generation plan could not be read, so this dialog cannot say which
          sub-projects will regenerate. Generating still works — the run decides for
          itself.
        </p>
        <button type="button" className="btn-ghost" onClick={onRetry}>
          Retry
        </button>
      </div>
    );
  }

  // Read, but nothing to preview: a single-run project, or one with no scan yet.
  if (!plan) return null;

  // An action this build has not been taught counts as generate. Over-stating
  // cost is recoverable; a surprise full-price run is not.
  const willReuse = plan.partitions.filter((p) => p.action === "reuse");
  const willGenerate = plan.partitions.filter((p) => p.action !== "reuse");

  return (
    <section className="space-y-3 rounded border border-white/[0.08] bg-black/10 p-4">
      <div>
        <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
          Generation plan
        </h4>
        <p className="mt-1 text-sm text-slate-200">
          <span className="text-base font-semibold text-accent-primary">
            {plan.will_generate} of {plan.partition_count}
          </span>{" "}
          sub-project{plan.partition_count === 1 ? "" : "s"} will regenerate
          {plan.will_reuse > 0 && (
            <>
              {" · "}
              {plan.will_reuse} reused at no model cost
            </>
          )}
        </p>
      </div>

      {plan.global_invalidation && (
        <div className="rounded border border-amber-400/30 bg-amber-400/5 p-3">
          <p className="text-sm text-amber-400">{plan.global_invalidation.detail}</p>
          <p className="mt-1 font-mono text-xs text-slate-500">
            {plan.global_invalidation.reason}
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <PartitionList
          title={`Will generate (${plan.will_generate})`}
          partitions={willGenerate}
          emptyLabel="Nothing needs regenerating."
          render={(partition) => (
            <>
              {partition.unit_count} unit{partition.unit_count === 1 ? "" : "s"}
              {" · "}
              <span className="font-mono">{partition.reason}</span>
            </>
          )}
        />
        <PartitionList
          title={`Will be reused (${plan.will_reuse})`}
          partitions={willReuse}
          emptyLabel="Nothing can be reused."
          render={(partition) => (
            <>
              {partition.doc_count} doc{partition.doc_count === 1 ? "" : "s"}
              {" · "}
              {formatGeneratedAt(partition.last_generated_at)}
              {" · "}
              <span className="font-mono">{partition.reason}</span>
            </>
          )}
        />
      </div>

      {plan.notes.length > 0 && (
        <ul className="space-y-1">
          {/* Keyed by position: the notes are server prose, and two identical
              strings must both render rather than collide on a key. */}
          {plan.notes.map((note, index) => (
            <li key={`${index}-${note}`} className="text-xs text-slate-500">
              {note}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function PartitionList({
  title,
  partitions,
  emptyLabel,
  render,
}: {
  title: string;
  partitions: GenerationPlanPartition[];
  emptyLabel: string;
  render: (partition: GenerationPlanPartition) => ReactNode;
}) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </p>
      {partitions.length === 0 ? (
        <p className="mt-1 text-xs text-slate-500">{emptyLabel}</p>
      ) : (
        <ul className="mt-1 space-y-1">
          {partitions.map((partition) => (
            <li key={partition.name} className="min-w-0">
              <p className="truncate text-sm text-slate-300">{partition.name}</p>
              <p className="truncate text-xs text-slate-500">{render(partition)}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Every live-data source this project may use, one row per catalogue provider.
 *
 * This replaced a panel that was generic in its loop and hardcoded everywhere
 * else: the empty state described any provider as Elastic, and the mirrored
 * runtime lane had no row at all -- just a floating checkbox that rendered
 * armed even for a project with no mirrored source registered, promising data
 * that did not exist.
 *
 * The rules now, all derived:
 *   - the row set comes from the connector catalogue, so a new provider appears
 *     with no change here;
 *   - `provider.kind` decides which engine reads it (`lib/connectors.ts`) and
 *     therefore whether the row is inert for the selected engine;
 *   - `provider.managed_by` decides WHERE its status is read from -- the
 *     runtime-sources store (mirrored, per project) or the connector store
 *     (in-band, per binding). That is a fact about the data, not the vendor.
 *
 * No provider name appears in a conditional or in copy meant to describe an
 * arbitrary provider. `tests/ep000-ui.test.tsx` renders a fixture provider that
 * is neither of the two shipped ones and asserts it comes out right; that test
 * is the guard against this regressing.
 */
function LiveDataSources({
  projectId,
  engine,
  liveEvidence,
  onLiveEvidenceChange,
  onManage,
}: {
  projectId: string;
  engine: GenerationEngine;
  liveEvidence: boolean;
  onLiveEvidenceChange: (value: boolean) => void;
  onManage?: () => void;
}) {
  const catalog = useConnectorCatalog();
  const connectors = useConnectors();
  const bindings = useProjectConnectorBindings(projectId);

  // `coming_soon` is a roadmap row, not something a run can read. Everything
  // else is listed -- including providers nothing is connected to -- because
  // "why can I not see it here" is exactly the question this panel answers.
  const providers = (catalog.data?.providers ?? []).filter(
    (provider) => provider.enabled && provider.build_state !== "coming_soon",
  );

  const isLoading = catalog.isLoading || connectors.isLoading || bindings.isLoading;
  const error = catalog.error ?? connectors.error ?? bindings.error;

  return (
    <section className="space-y-3 rounded border border-white/[0.08] bg-black/10 p-4">
      <div>
        <h4 className="font-mono uppercase tracking-wider text-xs text-accent-primary/80">
          Live data sources
        </h4>
        <p className="mt-1 text-xs text-slate-500">
          Safe, read-only observations Nexus may use to make this documentation
          more accurate. Which engine reads a source follows from its type, not
          from its vendor.
        </p>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-500">Checking live data sources…</p>
      ) : error ? (
        <div className="flex items-center justify-between gap-3 rounded border border-amber-400/30 bg-amber-400/5 p-3">
          <p className="text-sm text-amber-400">Live data sources could not be loaded.</p>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => {
              void catalog.refetch();
              void connectors.refetch();
              void bindings.refetch();
            }}
          >
            Retry
          </button>
        </div>
      ) : providers.length === 0 ? (
        <div className="rounded border border-dashed border-white/[0.1] p-3">
          <p className="text-sm text-slate-400">
            No live data providers are available in this Nexus build.
          </p>
          <p className="mt-1 text-xs text-slate-500">
            This run will use scanned source code only.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {providers.map((provider) => (
            <LiveDataSourceRow
              key={provider.provider_id}
              provider={provider}
              projectId={projectId}
              engine={engine}
              connectors={connectors.data?.connectors ?? []}
              bindings={bindings.data?.bindings ?? []}
              liveEvidence={liveEvidence}
              onLiveEvidenceChange={onLiveEvidenceChange}
            />
          ))}
        </div>
      )}

      {onManage && (
        <button type="button" className="btn-ghost" onClick={onManage}>
          Adjust connected systems
        </button>
      )}
    </section>
  );
}

function LiveDataSourceRow({
  provider,
  projectId,
  engine,
  connectors,
  bindings,
  liveEvidence,
  onLiveEvidenceChange,
}: {
  provider: ConnectorProvider;
  projectId: string;
  engine: GenerationEngine;
  connectors: ConnectorInstance[];
  bindings: ProjectConnectorBinding[];
  liveEvidence: boolean;
  onLiveEvidenceChange: (value: boolean) => void;
}) {
  // `managed_by` says which store holds this provider's credential, and so
  // which store can answer "is it configured for THIS project". Asked of every
  // provider it does not apply to, the runtime proxy would be a question with
  // no answer, so it is only asked of the ones it does.
  const mirrored = provider.managed_by === "runtime_sources";
  const runtime = useRuntimeSources(
    mirrored ? provider.provider_id : null,
    mirrored,
    projectId,
  );
  const sources = runtime.data?.sources ?? [];
  // Only meaningful once a mirror exists: with no source there is no slice to
  // describe, and asking would report the project's defaults as if they were a
  // decision someone made.
  const readScope = useRuntimeReadScope(projectId, mirrored && sources.length > 0);

  const instances = connectors.filter((item) => item.provider_id === provider.provider_id);
  const instanceIds = new Set(instances.map((item) => item.id));
  const binding = bindings.find(
    (item) =>
      item.enabled &&
      item.source_ids.length > 0 &&
      (item.connector_provider_id === provider.provider_id ||
        instanceIds.has(item.connector_instance_id)),
  );
  const boundInstance = instances.find((item) => item.id === binding?.connector_instance_id);

  const inert = !engineReadsKind(provider.kind, engine);
  // The toggle is armed only when there is a real mirrored source behind it.
  // An armed checkbox over an empty mirror was the panel's outright lie.
  const armed = mirrored && sources.length > 0;

  const displayName =
    binding?.connector_display_name ?? boundInstance?.display_name ?? provider.label;

  return (
    <div
      className={`rounded border border-white/[0.06] px-3 py-2 ${inert ? "opacity-60" : ""}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm text-slate-300">{displayName}</div>
          <div className="mt-0.5 truncate text-xs text-slate-500">
            {displayName === provider.label
              ? describeKind(provider)
              : `${provider.label} · ${describeKind(provider)}`}
          </div>
        </div>
        <span className="shrink-0 text-xs font-mono text-slate-500">
          {mirrored
            ? runtime.isLoading
              ? "Checking…"
              : runtime.isError
                ? "Status unavailable"
                : sources.length === 0
                  ? "No source for this project"
                  : `Mirroring ${sources.length} source${sources.length === 1 ? "" : "s"}`
            : binding
              ? `${boundInstance?.status === "connected" ? "Connected" : boundInstance?.status ?? "Configured"} · ${binding.source_ids.length} source${binding.source_ids.length === 1 ? "" : "s"}`
              : instances.length > 0
                ? "Not enabled here"
                : "Not connected"}
        </span>
      </div>

      {mirrored ? (
        runtime.isLoading ? null : runtime.isError ? (
          <p className="mt-1 text-xs text-amber-400">
            The mirror status could not be read, so this run&apos;s runtime history cannot be confirmed.
          </p>
        ) : sources.length === 0 ? (
          <>
            <p className="mt-1 text-xs text-slate-400">
              No mirrored source is registered for {projectId}, so this run has no{" "}
              {provider.label} history to read.
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              Register one on the Connectors page to give this project runtime history.
            </p>
          </>
        ) : (
          <ul className="mt-1 space-y-0.5">
            {sources.map((source) => (
              <li key={source.id} className="text-xs text-slate-400">
                {/* WHICH source and WHEN it last synced. "Connected" on its own
                    cannot answer "which one am I using".

                    The project is not repeated: this list is already narrowed
                    to sources attached to THIS project (`useRuntimeSources` is
                    called with `projectId`). The source's `estate_key` is a row
                    stamp, not a project, so printing it here would name
                    something that is not this run's project. */}
                {source.label || provider.label} · last synced{" "}
                {formatSyncedAt(source.last_synced_at)}
                {source.enabled ? (
                  source.status !== "success" && (
                    <span className="text-amber-400"> · {source.status}</span>
                  )
                ) : (
                  <span className="text-amber-400">
                    {" "}
                    · disabled{source.disabled_reason ? `: ${source.disabled_reason}` : ""}
                  </span>
                )}
              </li>
            ))}
            {readScope.data && (
              <li className="text-xs text-slate-400">
                {/* The scope decides what the run MEASURES. A project narrowed
                    to production reports production's failure rate; the same
                    project read wholesale reports a different number from the
                    same mirror. Stating it here is what makes two runs of the
                    same project comparable. */}
                Reads{" "}
                {readScope.data.environment_ids === null ||
                readScope.data.environment_names.length === 0
                  ? "every mirrored environment"
                  : readScope.data.environment_names.join(", ")}
                {readScope.data.window_days
                  ? ` · last ${readScope.data.window_days} days`
                  : ""}
                {readScope.data.unavailable_environment_ids.length > 0 && (
                  <span className="text-amber-400">
                    {" "}
                    · {readScope.data.unavailable_environment_ids.length} selected environment
                    {readScope.data.unavailable_environment_ids.length === 1 ? "" : "s"} not in the
                    mirror
                  </span>
                )}
              </li>
            )}
          </ul>
        )
      ) : binding ? null : (
        <p className="mt-1 text-xs text-slate-400">
          {instances.length > 0
            ? `${provider.label} is connected to Nexus, but it is not enabled for this project.`
            : `${provider.label} is not connected to Nexus yet.`}
        </p>
      )}

      <p className="mt-1 text-xs text-slate-500">{describeEngineReach(provider.kind, engine)}</p>

      {/* The toggle sits ON the row it controls, not floating under the list.
          Narrowly labelled on purpose: it gates ONLY the mirrored runtime lane,
          in both engines. A generic "live evidence off" would read as a kill
          switch for every connector, including in-band sources an OpenCode run
          queries through a different mechanism entirely. */}
      {armed && (
        <label className="mt-2 flex items-start gap-2.5 text-sm text-slate-300 select-none">
          <input
            type="checkbox"
            checked={liveEvidence}
            onChange={(event) => onLiveEvidenceChange(event.target.checked)}
            className="mt-0.5 h-4 w-4 accent-accent-primary"
          />
          <span>
            Use mirrored runtime history ({provider.label}) in this run
            <span className="mt-0.5 block text-xs text-slate-500">
              Applies to the mirrored runtime lane only. It does not change which
              connector sources this project has approved.
            </span>
          </span>
        </label>
      )}
    </div>
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
    provider: string;
    label: string;
    available: boolean;
    unavailable_reason: string | null;
  }[];
  placeholder: string;
}) {
  const groups = [
    {
      label: "OpenCode Go subscription",
      models: models.filter((model) => model.provider === "opencode-go"),
    },
    {
      label: "API providers",
      models: models.filter(
        (model) => model.provider !== "opencode-go" && !model.id.startsWith("ollama/"),
      ),
    },
    {
      label: "Local models",
      models: models.filter((model) => model.id.startsWith("ollama/")),
    },
  ].filter((group) => group.models.length > 0);

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="input-dark"
      aria-label={placeholder}
    >
      <option value="">{placeholder}</option>
      {groups.map((group) => (
        <optgroup key={group.label} label={group.label}>
          {group.models.map((model) => (
            <option key={model.id} value={model.id} disabled={!model.available}>
              {model.label} ({model.id})
              {model.available ? "" : ` — ${model.unavailable_reason ?? "credentials not configured"}`}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
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
