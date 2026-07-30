import { useEffect, useMemo, useState } from "react";
import { useProjects } from "../hooks/useProjects";
import {
  useAgenticExperiment,
  useAgenticExperimentCapabilities,
  useAgenticExperimentRetention,
  useAgenticExperiments,
  useAgenticExperimentTasks,
  useCompileAgenticRecipe,
  useCreateAgenticExperiment,
  useFreezeAgenticSourceSnapshot,
  useReconcileAgenticExperiment,
  useRetryAgenticExperimentTask,
} from "../hooks/useAgenticExperiments";
import { parseApiError } from "../lib/errors";
import type {
  AgenticExperiment,
  AgenticExperimentBranchMetrics,
  AgenticExperimentTask,
  AgenticRecipeCapability,
  AgenticSourceInput,
} from "../types";

const TERMINAL_STATUSES = new Set(["completed", "completed_warnings", "failed", "cancelled"]);

function unknown(value: string | number | null | undefined) {
  return value === null || value === undefined || value === "" ? "unknown" : String(value);
}

function stableIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.()
    ?? `experiment-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function sourceKindFor(role: string, capabilities: string[]): AgenticSourceInput["source_kind"] {
  if (role === "implementation") return "intodoc_scan";
  if (capabilities.includes("runtime_events")) return "runtime_export";
  if (capabilities.includes("tabular_data")) return "tabular_file";
  return "directory_evidence";
}

function sourcesForRecipe(recipe: AgenticRecipeCapability): AgenticSourceInput[] {
  return recipe.source_requirements
    .filter((requirement) => !requirement.optional)
    .map((requirement, index) => {
      const source_kind = sourceKindFor(
        requirement.source_role,
        requirement.required_capabilities,
      );
      return {
        id: `${requirement.source_role}-${index + 1}`,
        source_kind,
        source_role: requirement.source_role,
        capabilities: requirement.required_capabilities,
        ...(source_kind === "intodoc_scan"
          ? {}
          : { governed_root_index: 0, relative_path: "" }),
      };
    });
}

function sourceIsReady(source: AgenticSourceInput) {
  return source.source_kind === "intodoc_scan"
    || (source.governed_root_index !== undefined && Boolean(source.relative_path?.trim()));
}

function AttemptMetrics({ task }: { task: AgenticExperimentTask }) {
  const attempts = task.attempts;
  const sumKnown = (field: "input_tokens" | "output_tokens") => attempts.length
    && attempts.every((attempt) => attempt[field] !== null)
      ? attempts.reduce((sum, attempt) => sum + (attempt[field] ?? 0), 0)
      : null;
  const cost = attempts.length
    && attempts.every((attempt) => attempt.provider_cost_usd !== null)
      ? attempts.reduce((sum, attempt) => sum + Number(attempt.provider_cost_usd), 0)
      : null;
  return (
    <div className="grid grid-cols-3 gap-2 text-xs text-slate-500 font-mono">
      <span>in: {unknown(sumKnown("input_tokens"))}</span>
      <span>out: {unknown(sumKnown("output_tokens"))}</span>
      <span>cost: {cost === null ? "unknown" : `$${cost.toFixed(4)}`}</span>
    </div>
  );
}

export default function AgenticExperimentsPage() {
  const { data: projectsData } = useProjects();
  const projects = projectsData?.projects ?? [];
  const capabilitiesQuery = useAgenticExperimentCapabilities();
  const recipes = capabilitiesQuery.data?.recipes ?? [];
  const [projectId, setProjectId] = useState("");
  const [selectedExperimentId, setSelectedExperimentId] = useState<string | null>(null);
  const [acknowledgeNonPublishing, setAcknowledgeNonPublishing] = useState(false);
  const [recipeId, setRecipeId] = useState("");
  const [sources, setSources] = useState<AgenticSourceInput[]>([]);
  const [scopeJson, setScopeJson] = useState("{}");
  const [scopeError, setScopeError] = useState<string | null>(null);
  const [retryAcknowledged, setRetryAcknowledged] = useState<Record<string, boolean>>({});
  const [reconcileAcknowledged, setReconcileAcknowledged] = useState(false);
  const [retentionAcknowledged, setRetentionAcknowledged] = useState(false);
  const [retentionState, setRetentionState] = useState<"active" | "quarantined">("active");
  const experimentsQuery = useAgenticExperiments(projectId || null);
  const experimentQuery = useAgenticExperiment(selectedExperimentId);
  const tasksQuery = useAgenticExperimentTasks(selectedExperimentId);
  const createExperiment = useCreateAgenticExperiment();
  const freezeSources = useFreezeAgenticSourceSnapshot();
  const compileRecipe = useCompileAgenticRecipe();
  const retryTask = useRetryAgenticExperimentTask();
  const reconcile = useReconcileAgenticExperiment();
  const retention = useAgenticExperimentRetention();

  useEffect(() => {
    if (!projectId && projects[0]) setProjectId(projects[0].id);
  }, [projectId, projects]);

  useEffect(() => {
    if (!recipeId && recipes[0]) setRecipeId(recipes[0].recipe_id);
  }, [recipeId, recipes]);

  useEffect(() => {
    const experiments = experimentsQuery.data ?? [];
    if (
      experiments.length
      && !experiments.some((item) => item.experiment_id === selectedExperimentId)
    ) {
      setSelectedExperimentId(experiments[0].experiment_id);
    }
  }, [experimentsQuery.data, selectedExperimentId]);

  const selectedRecipe = recipes.find((recipe) => recipe.recipe_id === recipeId) ?? null;
  const experiment = experimentQuery.data;
  const tasks = tasksQuery.data?.tasks ?? [];
  const sourceSnapshotFrozen = Boolean(experiment?.source_snapshot_frozen);
  const recipeCompiled = Boolean(experiment?.recipe_snapshot_id);

  useEffect(() => {
    if (selectedRecipe && !sourceSnapshotFrozen) {
      setSources(sourcesForRecipe(selectedRecipe));
    }
  }, [selectedRecipe, sourceSnapshotFrozen]);

  const artifactGroups = useMemo(() => ({
    baseline: experiment?.artifacts.filter((artifact) => artifact.branch === "baseline") ?? [],
    candidate: experiment?.artifacts.filter((artifact) => artifact.branch === "candidate") ?? [],
  }), [experiment]);

  function updateSource(index: number, patch: Partial<AgenticSourceInput>) {
    setSources((current) => current.map((source, itemIndex) => (
      itemIndex === index ? { ...source, ...patch } : source
    )));
  }

  async function handleCreate() {
    if (!projectId || !acknowledgeNonPublishing) return;
    const created = await createExperiment.mutateAsync({
      project_id: projectId,
      recipe_id: selectedRecipe?.recipe_id ?? null,
      recipe_version: selectedRecipe?.recipe_version ?? null,
      runtime_id: "opencode",
      acknowledge_non_publishing: true,
      idempotency_key: stableIdempotencyKey(),
    });
    setSelectedExperimentId(created.experiment_id);
    setRetentionState("active");
  }

  async function handleFreezeSources() {
    if (!selectedExperimentId || !sources.length || !sources.every(sourceIsReady)) return;
    await freezeSources.mutateAsync({ experimentId: selectedExperimentId, sources });
    await experimentQuery.refetch();
  }

  async function handleCompile() {
    if (!selectedExperimentId || !selectedRecipe) return;
    let scope: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(scopeJson);
      if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error("scope must be a JSON object");
      }
      scope = parsed as Record<string, unknown>;
      setScopeError(null);
    } catch (error) {
      setScopeError(error instanceof Error ? error.message : "scope is invalid");
      return;
    }
    await compileRecipe.mutateAsync({
      experimentId: selectedExperimentId,
      recipe: {
        recipe_id: selectedRecipe.recipe_id,
        recipe_version: selectedRecipe.recipe_version,
        model_capabilities: selectedRecipe.required_model_capabilities,
        scope,
      },
    });
    await Promise.all([experimentQuery.refetch(), tasksQuery.refetch()]);
  }

  const blockers = capabilitiesQuery.data?.execution_blockers ?? [
    "Loading execution capability gate",
  ];
  const retentionError = retention.quarantine.error ?? retention.restore.error;

  return (
    <div className="space-y-5" data-testid="agentic-experiments-page">
      <section className="card-dark p-4 space-y-3 border border-amber-400/20">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg text-slate-100">Agentic Experiments</h2>
            <p className="text-sm text-slate-500 mt-1">
              Isolated, non-publishing A/B workspace. Scanner output, legacy generation,
              published documentation, and RAG remain unchanged.
            </p>
          </div>
          <span className="text-xs font-mono text-amber-300 border border-amber-400/30 rounded px-2 py-1">
            OpenCode {unknown(capabilitiesQuery.data?.opencode_version)} / provider-neutral
          </span>
        </div>
        <div className="text-sm text-slate-400 bg-white/[0.03] rounded p-3">
          <p>
            {capabilitiesQuery.data?.candidate_dispatch_available
              ? "OpenCode candidates can be started from the normal Generate Docs dialog. This workspace remains the inspection and comparison view."
              : "Candidate execution is unavailable. This workspace can still freeze, compile, inspect, recover, and explicitly retry."}
          </p>
          <ul className="mt-1 list-disc list-inside text-xs text-slate-500">
            {blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
          </ul>
        </div>
      </section>

      <section className="card-dark p-4 space-y-4">
        <div className="grid md:grid-cols-[minmax(0,1fr)_auto] gap-4 items-end">
          <label className="block text-sm text-slate-300">
            Project
            <select
              aria-label="Experiment project"
              className="input-dark w-full mt-1"
              value={projectId}
              onChange={(event) => {
                setProjectId(event.target.value);
                setSelectedExperimentId(null);
              }}
            >
              <option value="">Select a project</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.display_name} ({project.id})
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn-primary"
            onClick={handleCreate}
            disabled={!projectId || !acknowledgeNonPublishing || createExperiment.isPending}
          >
            {createExperiment.isPending ? "Freezing baseline…" : "Create & freeze latest baseline"}
          </button>
        </div>
        <label className="flex items-start gap-2 text-sm text-slate-400 cursor-pointer">
          <input
            aria-label="Acknowledge non-publishing experiment"
            type="checkbox"
            checked={acknowledgeNonPublishing}
            onChange={(event) => setAcknowledgeNonPublishing(event.target.checked)}
          />
          <span>I acknowledge candidate artifacts remain isolated and never replace published docs or enter RAG.</span>
        </label>
        {createExperiment.isError && (
          <p className="text-sm text-red-400">Create failed: {parseApiError(createExperiment.error)}</p>
        )}
      </section>

      <div className="grid lg:grid-cols-[minmax(18rem,0.75fr)_minmax(0,1.5fr)] gap-5">
        <section className="card-dark p-4">
          <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">Experiments</h3>
          {experimentsQuery.isLoading && <p className="text-sm text-slate-500 mt-3">Loading…</p>}
          {experimentsQuery.isError && (
            <p className="text-sm text-red-400 mt-3">Unable to load: {parseApiError(experimentsQuery.error)}</p>
          )}
          {!experimentsQuery.isLoading && !experimentsQuery.isError
            && (experimentsQuery.data?.length ?? 0) === 0 && (
            <p className="text-sm text-slate-600 mt-3">No isolated experiments for this project.</p>
          )}
          <div className="mt-3 space-y-2">
            {(experimentsQuery.data ?? []).map((item) => (
              <button
                key={item.experiment_id}
                className={`w-full text-left rounded border p-3 transition-colors ${
                  selectedExperimentId === item.experiment_id
                    ? "border-accent-primary/60 bg-accent-primary/10"
                    : "border-white/[0.08] hover:border-white/[0.18]"
                }`}
                onClick={() => {
                  setSelectedExperimentId(item.experiment_id);
                  setRetentionState("active");
                }}
              >
                <div className="flex justify-between gap-2 text-sm text-slate-200">
                  <span className="font-mono truncate">{item.experiment_id}</span>
                  <span>{item.status}</span>
                </div>
                <div className="text-xs text-slate-500 mt-1">
                  baseline: {unknown(item.baseline_run_id)} · docs: {item.baseline_document_count}
                </div>
              </button>
            ))}
          </div>
        </section>

        <section className="card-dark p-4 space-y-5">
          {!selectedExperimentId && (
            <p className="text-sm text-slate-600">Select or create an experiment.</p>
          )}
          {selectedExperimentId && experimentQuery.isLoading && (
            <p className="text-sm text-slate-500">Loading experiment…</p>
          )}
          {experimentQuery.isError && (
            <p className="text-sm text-red-400">Unable to load: {parseApiError(experimentQuery.error)}</p>
          )}
          {experiment && (
            <>
              <div className="flex flex-wrap justify-between gap-2">
                <div>
                  <h3 className="text-base text-slate-100">Experiment {experiment.experiment_id}</h3>
                  <p className="text-xs text-slate-500 mt-1">
                    publishes: false · verdict: {experiment.candidate_verdict} · runtime: {experiment.runtime_id}
                  </p>
                </div>
                <span className="text-xs font-mono text-slate-400">
                  baseline manifest: {unknown(experiment.baseline_manifest_sha256)}
                </span>
              </div>
              {experiment.error_message && <p className="text-sm text-red-400">{experiment.error_message}</p>}
              {experiment.safety_warnings.length > 0 && (
                <section className="rounded border border-yellow-400/30 bg-yellow-400/[0.06] p-3">
                  <h3 className="text-sm font-medium text-yellow-200">
                    Completed with safety warnings ({experiment.safety_warnings.length})
                  </h3>
                  <p className="mt-1 text-xs text-slate-400">
                    These are flagged references in generated content; the candidate remains isolated and is not published or sent to RAG.
                  </p>
                  <ul className="mt-2 space-y-1 text-xs text-yellow-100/80">
                    {experiment.safety_warnings.map((warning, index) => (
                      <li key={`${warning.path}-${warning.category}-${index}`}>
                        {warning.category} · {warning.path}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {experiment.grounded_progress && (
                <section className="rounded border border-accent-primary/25 bg-accent-primary/[0.04] p-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-medium text-slate-100">
                      Grounded stage: {experiment.grounded_progress.stage}
                    </h3>
                    <span className="text-xs font-mono text-slate-400">
                      {experiment.grounded_progress.accepted_artifact_count}/
                      {experiment.grounded_progress.required_artifact_count} artifacts
                    </span>
                  </div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {experiment.grounded_progress.roles.map((role) => (
                      <div
                        key={role.role_id}
                        className="rounded border border-white/[0.07] px-2.5 py-2 text-xs"
                      >
                        <div className="flex justify-between gap-2 text-slate-300">
                          <span className="font-mono">{role.role_id}</span>
                          <span>{role.status}</span>
                        </div>
                        <div className="mt-1 text-slate-500">
                          {role.stage} · {role.accepted_artifact_count}/
                          {role.owned_artifact_count} owned artifacts
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="mt-2 text-xs text-slate-500">
                    Progress reflects accepted files only. Agent reasoning and source contents are not exposed.
                  </p>
                </section>
              )}

              <RecipeAndSources
                recipes={recipes}
                recipeId={recipeId}
                setRecipeId={setRecipeId}
                selectedRecipe={selectedRecipe}
                sources={sources}
                updateSource={updateSource}
                addSource={() => setSources((current) => [...current, {
                  id: `custom-${current.length + 1}`,
                  source_kind: "directory_evidence",
                  source_role: "reference",
                  capabilities: ["searchable_text"],
                  governed_root_index: 0,
                  relative_path: "",
                }])}
                removeSource={(index) => setSources((current) => current.filter((_, item) => item !== index))}
                frozen={sourceSnapshotFrozen}
                recipeCompiled={recipeCompiled}
                freezePending={freezeSources.isPending}
                compilePending={compileRecipe.isPending}
                sourcesReady={sources.length > 0 && sources.every(sourceIsReady)}
                onFreeze={handleFreezeSources}
                onCompile={handleCompile}
                scopeJson={scopeJson}
                setScopeJson={setScopeJson}
                scopeError={scopeError}
              />
              {(freezeSources.isError || compileRecipe.isError) && (
                <p className="text-sm text-red-400">
                  {freezeSources.isError
                    ? `Source freeze failed: ${parseApiError(freezeSources.error)}`
                    : `Recipe compile failed: ${parseApiError(compileRecipe.error)}`}
                </p>
              )}

              {experiment.runtime_id === "opencode" && tasks.length > 0
                && tasks.every((task) => task.status === "pending") ? (
                <section className="rounded border border-accent-primary/30 bg-accent-primary/[0.05] px-4 py-3">
                  <h3 className="text-sm font-medium text-slate-100">
                    OpenCode documentation agent: {experiment.status}
                  </h3>
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    One agent is investigating the frozen workspace and writing the complete candidate bundle.
                    The recipe nodes below are its blueprint, not separately dispatched tasks.
                  </p>
                </section>
              ) : (
                <>
                  <button className="btn-primary" disabled>
                    Start candidate — gated
                  </button>
                  <TaskProgress
                    tasks={tasks}
                    retryAcknowledged={retryAcknowledged}
                    setRetryAcknowledged={setRetryAcknowledged}
                    retryTask={retryTask}
                    experimentId={experiment.experiment_id}
                  />
                </>
              )}
              <RecoveryAndRetention
                experiment={experiment}
                taskCount={tasks.length}
                reconcileAcknowledged={reconcileAcknowledged}
                setReconcileAcknowledged={setReconcileAcknowledged}
                reconcile={reconcile}
                retentionAcknowledged={retentionAcknowledged}
                setRetentionAcknowledged={setRetentionAcknowledged}
                retentionState={retentionState}
                onQuarantine={async () => {
                  await retention.quarantine.mutateAsync(experiment.experiment_id);
                  setRetentionState("quarantined");
                }}
                onRestore={async () => {
                  await retention.restore.mutateAsync(experiment.experiment_id);
                  setRetentionState("active");
                }}
                retentionPending={retention.quarantine.isPending || retention.restore.isPending}
                retentionError={retentionError}
              />
              <ExperimentMetrics
                baseline={experiment.baseline_metrics}
                candidate={experiment.candidate_metrics}
              />
              <ArtifactMetrics title="Baseline artifacts" artifacts={artifactGroups.baseline} empty="No baseline artifacts returned." />
              <ArtifactMetrics title="Candidate artifacts" artifacts={artifactGroups.candidate} empty="unknown — candidate execution has not started." />
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function RecipeAndSources({
  recipes,
  recipeId,
  setRecipeId,
  selectedRecipe,
  sources,
  updateSource,
  addSource,
  removeSource,
  frozen,
  recipeCompiled,
  freezePending,
  compilePending,
  sourcesReady,
  onFreeze,
  onCompile,
  scopeJson,
  setScopeJson,
  scopeError,
}: {
  recipes: AgenticRecipeCapability[];
  recipeId: string;
  setRecipeId: (value: string) => void;
  selectedRecipe: AgenticRecipeCapability | null;
  sources: AgenticSourceInput[];
  updateSource: (index: number, patch: Partial<AgenticSourceInput>) => void;
  addSource: () => void;
  removeSource: (index: number) => void;
  frozen: boolean;
  recipeCompiled: boolean;
  freezePending: boolean;
  compilePending: boolean;
  sourcesReady: boolean;
  onFreeze: () => void;
  onCompile: () => void;
  scopeJson: string;
  setScopeJson: (value: string) => void;
  scopeError: string | null;
}) {
  return (
    <section className="space-y-3 rounded border border-white/[0.08] p-3">
      <div className="grid md:grid-cols-2 gap-3">
        <label className="text-sm text-slate-300">
          Certified recipe
          <select
            aria-label="Agentic recipe"
            className="input-dark w-full mt-1"
            value={recipeId}
            disabled={frozen}
            onChange={(event) => setRecipeId(event.target.value)}
          >
            {recipes.map((recipe) => (
              <option key={`${recipe.recipe_id}:${recipe.recipe_version}`} value={recipe.recipe_id}>
                {recipe.recipe_id} v{recipe.recipe_version} ({recipe.document_output_count} docs)
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm text-slate-300">
          Generic scope (JSON)
          <textarea
            aria-label="Generic experiment scope"
            className="input-dark w-full mt-1 font-mono text-xs"
            value={scopeJson}
            disabled={recipeCompiled}
            onChange={(event) => setScopeJson(event.target.value)}
          />
        </label>
      </div>
      {scopeError && <p className="text-xs text-red-400">{scopeError}</p>}
      <div className="flex items-center justify-between">
        <h4 className="text-sm text-slate-300">Composable source snapshot</h4>
        <button className="text-xs text-accent-primary disabled:text-slate-700" disabled={frozen} onClick={addSource}>
          Add source
        </button>
      </div>
      {sources.map((source, index) => (
        <div key={source.id} className="grid md:grid-cols-[1fr_1fr_1.4fr_auto] gap-2 items-end">
          <label className="text-xs text-slate-500">
            Role
            <input
              className="input-dark w-full mt-1"
              value={source.source_role}
              disabled={frozen}
              onChange={(event) => updateSource(index, { source_role: event.target.value })}
            />
          </label>
          <label className="text-xs text-slate-500">
            Kind
            <select
              className="input-dark w-full mt-1"
              value={source.source_kind}
              disabled={frozen}
              onChange={(event) => {
                const source_kind = event.target.value as AgenticSourceInput["source_kind"];
                updateSource(index, source_kind === "intodoc_scan"
                  ? { source_kind, governed_root_index: undefined, relative_path: undefined }
                  : { source_kind, governed_root_index: 0, relative_path: source.relative_path ?? "" });
              }}
            >
              <option value="intodoc_scan">IntoDoc scan</option>
              <option value="directory_evidence">Directory evidence</option>
              <option value="tabular_file">Tabular file</option>
              <option value="runtime_export">Runtime export</option>
            </select>
          </label>
          <label className="text-xs text-slate-500">
            {source.source_kind === "intodoc_scan" ? "Capabilities" : "Governed relative path"}
            {source.source_kind === "intodoc_scan" ? (
              <input className="input-dark w-full mt-1" value={source.capabilities.join(", ")} disabled />
            ) : (
              <input
                className="input-dark w-full mt-1"
                placeholder="folder/file relative to governed root 0"
                value={source.relative_path ?? ""}
                disabled={frozen}
                onChange={(event) => updateSource(index, {
                  governed_root_index: 0,
                  relative_path: event.target.value,
                })}
              />
            )}
          </label>
          <button className="text-xs text-red-400 disabled:text-slate-700" disabled={frozen} onClick={() => removeSource(index)}>
            Remove
          </button>
          <p className="md:col-span-4 text-[11px] text-slate-600">
            required capabilities: {source.capabilities.join(", ") || "none"}
          </p>
        </div>
      ))}
      {selectedRecipe && (
        <p className="text-xs text-slate-600">
          Outputs: {selectedRecipe.output_ids.join(", ")}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button className="btn-ghost" onClick={onFreeze} disabled={frozen || !sourcesReady || freezePending}>
          {frozen ? "Sources frozen" : freezePending ? "Freezing…" : "1. Freeze sources"}
        </button>
        <button className="btn-ghost" onClick={onCompile} disabled={!frozen || recipeCompiled || compilePending}>
          {recipeCompiled ? "Recipe compiled" : compilePending ? "Compiling…" : "2. Compile recipe"}
        </button>
      </div>
    </section>
  );
}

function TaskProgress({
  tasks,
  retryAcknowledged,
  setRetryAcknowledged,
  retryTask,
  experimentId,
}: {
  tasks: AgenticExperimentTask[];
  retryAcknowledged: Record<string, boolean>;
  setRetryAcknowledged: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  retryTask: ReturnType<typeof useRetryAgenticExperimentTask>;
  experimentId: string;
}) {
  return (
    <section className="space-y-3">
      <div className="flex justify-between gap-3">
        <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">Tasks, attempts, and events</h3>
        <span className="text-xs text-slate-600">{tasks.length} task(s)</span>
      </div>
      {tasks.length === 0 ? (
        <p className="text-sm text-slate-600">No compiled task progress yet.</p>
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => {
            const retryable = task.status === "failed" && task.attempt < task.max_attempts;
            return (
              <div key={task.task_run_id} className="rounded border border-white/[0.08] p-3">
                <div className="flex flex-wrap justify-between gap-2">
                  <span className="text-sm text-slate-200 font-mono">{task.node_id}</span>
                  <span className="text-xs text-slate-400">{task.status} · {task.attempt}/{task.max_attempts}</span>
                </div>
                <p className="text-xs text-slate-500 mt-1">
                  outputs: {task.output_ids.join(", ") || "none"} · dependencies: {task.dependencies.join(", ") || "none"}
                </p>
                <div className="mt-2"><AttemptMetrics task={task} /></div>
                <p className="text-xs text-slate-600 mt-2">
                  events: {task.events.length ? task.events.map((event) => event.event_type).join(", ") : "unknown"}
                </p>
                {retryable && (
                  <div className="mt-2">
                    <label className="inline-flex items-center gap-2 text-xs text-slate-500">
                      <input
                        aria-label={`Acknowledge retry ${task.node_id}`}
                        type="checkbox"
                        checked={Boolean(retryAcknowledged[task.task_run_id])}
                        onChange={(event) => setRetryAcknowledged((current) => ({
                          ...current,
                          [task.task_run_id]: event.target.checked,
                        }))}
                      />
                      Explicitly authorize one new attempt.
                    </label>
                    <button
                      className="ml-3 text-xs text-amber-300 disabled:text-slate-700"
                      disabled={!retryAcknowledged[task.task_run_id] || retryTask.isPending}
                      onClick={() => retryTask.mutate({ experimentId, taskRunId: task.task_run_id })}
                    >
                      Request retry
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {retryTask.isError && <p className="text-sm text-red-400">Retry failed: {parseApiError(retryTask.error)}</p>}
    </section>
  );
}

function RecoveryAndRetention({
  experiment,
  taskCount,
  reconcileAcknowledged,
  setReconcileAcknowledged,
  reconcile,
  retentionAcknowledged,
  setRetentionAcknowledged,
  retentionState,
  onQuarantine,
  onRestore,
  retentionPending,
  retentionError,
}: {
  experiment: AgenticExperiment;
  taskCount: number;
  reconcileAcknowledged: boolean;
  setReconcileAcknowledged: (value: boolean) => void;
  reconcile: ReturnType<typeof useReconcileAgenticExperiment>;
  retentionAcknowledged: boolean;
  setRetentionAcknowledged: (value: boolean) => void;
  retentionState: "active" | "quarantined";
  onQuarantine: () => Promise<void>;
  onRestore: () => Promise<void>;
  retentionPending: boolean;
  retentionError: Error | null;
}) {
  return (
    <section className="rounded border border-white/[0.08] p-3 space-y-2">
      <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">Recovery and retention</h3>
      <label className="flex items-center gap-2 text-xs text-slate-500">
        <input
          type="checkbox"
          aria-label="Acknowledge reconciliation performs no dispatch"
          checked={reconcileAcknowledged}
          onChange={(event) => setReconcileAcknowledged(event.target.checked)}
        />
        Reconcile expired leases only; do not dispatch, retry, or publish.
      </label>
      <button
        className="btn-ghost"
        disabled={!taskCount || !reconcileAcknowledged || reconcile.isPending}
        onClick={() => reconcile.mutate(experiment.experiment_id)}
      >
        Reconcile task ledger
      </button>
      {reconcile.data && (
        <p className="text-xs text-slate-500">
          derived: {reconcile.data.derived_status}; manual retry: {reconcile.data.manual_retry_task_ids.join(", ") || "none"}
        </p>
      )}
      {reconcile.isError && <p className="text-xs text-red-400">{parseApiError(reconcile.error)}</p>}

      {TERMINAL_STATUSES.has(experiment.status) && (
        <>
          <label className="flex items-center gap-2 text-xs text-slate-500">
            <input
              type="checkbox"
              aria-label="Acknowledge recoverable retention move"
              checked={retentionAcknowledged}
              onChange={(event) => setRetentionAcknowledged(event.target.checked)}
            />
            Retention only moves this isolated experiment; it never hard-deletes or touches legacy docs.
          </label>
          <button
            className="btn-ghost"
            disabled={!retentionAcknowledged || retentionPending}
            onClick={() => void (retentionState === "active" ? onQuarantine() : onRestore())}
          >
            {retentionState === "active" ? "Quarantine old experiment" : "Restore experiment"}
          </button>
        </>
      )}
      {retentionError && <p className="text-xs text-red-400">{parseApiError(retentionError)}</p>}
    </section>
  );
}

function ExperimentMetrics({
  baseline,
  candidate,
}: {
  baseline: AgenticExperimentBranchMetrics;
  candidate: AgenticExperimentBranchMetrics;
}) {
  const rows: Array<[string, keyof AgenticExperimentBranchMetrics]> = [
    ["Documents", "document_count"],
    ["Duration (ms)", "duration_ms"],
    ["Provider latency (ms)", "provider_latency_ms"],
    ["Input tokens", "input_tokens"],
    ["Output tokens", "output_tokens"],
    ["Billed cost (USD)", "billed_cost_usd"],
    ["Quality", "quality_score"],
    ["Model", "model_id"],
    ["Attempts", "attempt_count"],
    ["Retries", "retry_count"],
  ];
  return (
    <section>
      <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">Baseline vs candidate metrics</h3>
      <table className="mt-2 w-full text-xs text-slate-500">
        <thead>
          <tr className="text-left text-slate-600"><th>Metric</th><th>Baseline</th><th>Candidate</th></tr>
        </thead>
        <tbody>
          {rows.map(([label, field]) => (
            <tr key={field}>
              <td>{label}</td>
              <td>{unknown(baseline[field])}</td>
              <td>{unknown(candidate[field])}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-xs text-slate-600">Unknown stays unknown; provider billing and normalized evaluation cost are never inferred.</p>
    </section>
  );
}

function ArtifactMetrics({
  title,
  artifacts,
  empty,
}: {
  title: string;
  artifacts: { logical_name: string; kind: string; size_bytes: number; sha256: string }[];
  empty: string;
}) {
  return (
    <section>
      <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">{title}</h3>
      {artifacts.length === 0 ? (
        <p className="text-sm text-slate-600 mt-2">{empty}</p>
      ) : (
        <ul className="mt-2 space-y-1 text-xs text-slate-500 font-mono">
          {artifacts.map((artifact) => (
            <li key={`${artifact.kind}:${artifact.logical_name}`}>
              {artifact.logical_name} · {artifact.kind} · {artifact.size_bytes} bytes · {artifact.sha256}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
