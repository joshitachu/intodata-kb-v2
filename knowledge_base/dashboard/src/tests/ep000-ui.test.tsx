import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import ABComparison from "../components/ABComparison";
import GenerateDialog from "../components/GenerateDialog";
import ProjectSettings from "../components/ProjectSettings";
import ValidationReviewPanel from "../components/ValidationReviewPanel";
import { api } from "../api/client";
import type {
  ConnectorInstance,
  ConnectorProvider,
  Project,
  ProjectConnectorBinding,
  RuntimeSource,
} from "../types";

vi.mock("../api/client", () => ({
  api: {
    listGenerateRuns: vi.fn(),
    compareGenerateRuns: vi.fn(),
    listPlatforms: vi.fn(),
    listModels: vi.fn(),
    getProjectUsage: vi.fn(),
    getInstructionPack: vi.fn(),
    getQuestionnaire: vi.fn(),
    getReviews: vi.fn(),
    submitReview: vi.fn(),
    updateProject: vi.fn(),
    deleteProject: vi.fn(),
    listProjects: vi.fn(),
    listPluginPacks: vi.fn(),
    assignPluginPack: vi.fn(),
    generate: vi.fn(),
    listConnectorCatalog: vi.fn(),
    listConnectors: vi.fn(),
    listProjectConnectorBindings: vi.fn(),
    listRuntimeSources: vi.fn(),
    listConnectorSources: vi.fn(),
    listProjectAvailableConnectors: vi.fn(),
    putProjectConnectorBinding: vi.fn(),
    deleteProjectConnectorBinding: vi.fn(),
    getRunConnectorEvidence: vi.fn(),
    getGenerationPlan: vi.fn(),
    listMultiRepoMembers: vi.fn(),
  },
}));

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const runs = {
  runs: [
    {
      id: 1,
      project_id: "p1",
      triggered_by: "api",
      status: "completed",
      started_at: "2026-01-01T00:00:00Z",
      completed_at: "2026-01-01T00:01:00Z",
      cost_usd: 1,
      duration_ms: 1000,
      duration_api_ms: null,
      input_tokens: 100,
      output_tokens: 50,
      num_turns: null,
      session_id: null,
      doc_count: 2,
      error_message: null,
      log_file: null,
      model_name: "moonshot/kimi-k2.6",
      write_model_name: "openai/deepseek-v4-pro",
      quality_score: 4.5,
      auto_verdict: null,
      auto_baseline_run_id: null,
      run_verdict: null,
      gate_results: null,
    },
    {
      id: 2,
      project_id: "p1",
      triggered_by: "api",
      status: "completed",
      started_at: "2026-01-02T00:00:00Z",
      completed_at: "2026-01-02T00:01:00Z",
      cost_usd: 2,
      duration_ms: 1200,
      duration_api_ms: null,
      input_tokens: 120,
      output_tokens: 60,
      num_turns: null,
      session_id: null,
      doc_count: 2,
      error_message: null,
      log_file: null,
      model_name: "openai/gpt-4o",
      write_model_name: "anthropic/claude-sonnet-4-6",
      quality_score: 3.9,
      auto_verdict: null,
      auto_baseline_run_id: null,
      run_verdict: null,
      gate_results: null,
    },
  ],
  total: 2,
};

const neutralModels = {
  models: [
    {
      id: "provider-neutral-test/model-neutral-test",
      provider: "provider-neutral-test",
      label: "Neutral Test Model",
      supports_analysis: true,
      supports_writing: true,
      is_default_analysis: true,
      is_default_investigation: true,
      is_default_writing: true,
      is_local: false,
      available: true,
      unavailable_reason: null,
      rag_capable: true,
    },
  ],
};

const workspaceProject: Project = {
  id: "p1",
  display_name: "Workspace Project",
  repo_url: null,
  local_path: "/tmp/p1",
  platform: "talend",
  doc_generation_provider: null,
  source_type: "zip",
  status: "ready",
  last_scan_at: null,
  last_generate_at: null,
  last_rag_push_at: null,
  job_count: 2,
  doc_count: 1,
  error_message: null,
  last_ingested_commit_sha: null,
  auto_reingest_enabled: false,
  auto_reingest_last_checked_at: null,
  auto_reingest_error: null,
  git_update_summary: null,
  has_github_token: false,
  connection_kind: "pat",
  github_account_login: null,
  webhook_status: null,
  instruction_pack_dir: null,
  plugin_pack_id: null,
  created_at: null,
  updated_at: null,
};

/**
 * Two live-data providers, NEITHER of which this build ships.
 *
 * That is the point. The Generate dialog's live-data panel used to describe any
 * provider with the word "Elastic" and gave the mirrored lane no row at all --
 * just a checkbox that named one vendor and rendered armed even when nothing
 * was registered. These fixtures are the regression guard: if a provider name
 * ever creeps back into a conditional or into copy meant to describe an
 * arbitrary provider, these two rows will not render correctly.
 */
function provider(overrides: Partial<ConnectorProvider> & Pick<ConnectorProvider, "provider_id" | "label">): ConnectorProvider {
  return {
    version: "1",
    authentication_types: ["api_key"],
    capabilities: [],
    enabled: true,
    kind: "live_evidence",
    build_state: "available",
    managed_by: "connector_store",
    description: "",
    ...overrides,
  };
}

/** Queried in-band during a run. Read by OpenCode only. */
const inBandProvider = provider({
  provider_id: "acme_telemetry",
  label: "Acme Telemetry",
  kind: "live_evidence",
  capabilities: [
    { id: "search", label: "Search events", description: null, read_only: true, gather_eligible: true },
    { id: "aggregate", label: "Aggregate events", description: null, read_only: true, gather_eligible: true },
  ],
});

/** Mirrored on a schedule into the runtime store. Read by both engines. */
const mirroredProvider = provider({
  provider_id: "northwind_scheduler",
  label: "Northwind Scheduler",
  kind: "runtime_sync",
  managed_by: "runtime_sources",
});

const inBandInstance: ConnectorInstance = {
  id: "conn-acme-1",
  provider_id: "acme_telemetry",
  display_name: "Acme Telemetry (eu)",
  ownership_scope: "workspace",
  enabled: true,
  status: "connected",
  last_checked_at: null,
  health_code: null,
  provider_version: "1",
  redaction_policy_version: null,
  has_credential: true,
  has_kibana_credential: false,
  created_at: null,
  updated_at: null,
};

const inBandBinding: ProjectConnectorBinding = {
  id: "bind-1",
  project_id: "p1",
  connector_instance_id: "conn-acme-1",
  connector_display_name: "Acme Telemetry (eu)",
  connector_provider_id: "acme_telemetry",
  source_ids: ["src_a", "src_b", "src_c"],
  capability_ids: ["search"],
  budgets: {},
  max_time_window_days: null,
  enabled: true,
  version: 1,
};

const MIRROR_SYNCED_AT = "2026-08-24T14:30:13Z";

const mirroredSource: RuntimeSource = {
  id: 77,
  project_id: "p1",
  source_type: "northwind",
  label: "Northwind prod (eu-west)",
  status: "success",
  enabled: true,
  sync_interval_seconds: 60,
  disabled_reason: null,
  last_synced_at: MIRROR_SYNCED_AT,
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listConnectorCatalog).mockResolvedValue({ providers: [] });
  vi.mocked(api.listConnectors).mockResolvedValue({ connectors: [], total: 0 });
  vi.mocked(api.listProjectConnectorBindings).mockResolvedValue({ project_id: "p1", bindings: [] });
  vi.mocked(api.listRuntimeSources).mockResolvedValue({ sources: [], total: 0 });
  vi.mocked(api.listProjectAvailableConnectors).mockResolvedValue({ connectors: [] });
  vi.mocked(api.listConnectorSources).mockResolvedValue({ connector_instance_id: "none", sources: [], total: 0 });
  vi.mocked(api.listPluginPacks).mockResolvedValue([]);
  // A single-run project: the Generate dialog's plan preview stays hidden and
  // the dialog behaves exactly as it did before that preview existed.
  vi.mocked(api.getGenerationPlan).mockResolvedValue({
    project_id: "p1",
    scan_available: true,
    composite: false,
    partition_count: 0,
    will_generate: 1,
    will_reuse: 0,
    global_invalidation: null,
    partitions: [],
    notes: [],
  });
  vi.mocked(api.listMultiRepoMembers).mockResolvedValue({
    project_id: "p1",
    members: [],
    sync: [],
  });
});

describe("EP-000 dashboard panels", () => {
  it("renders comparison verdict, models, and quality details", async () => {
    vi.mocked(api.listGenerateRuns).mockResolvedValue(runs);
    vi.mocked(api.compareGenerateRuns).mockResolvedValue({
      project_id: "p1",
      run_a: {
        ...runs.runs[0],
        run_id: 1,
        provider: "custom",
        analysis_model: "moonshot/kimi-k2.6",
        write_model: "openai/deepseek-v4-pro",
        quality: 4.5,
        quality_pass: true,
        doc_quality: { overall: 4.5, source_alignment: 4 },
        metrics_found: true,
        auto_verdict: null,
        auto_baseline_run_id: null,
      },
      run_b: {
        ...runs.runs[1],
        run_id: 2,
        provider: "custom",
        analysis_model: "openai/gpt-4o",
        write_model: "anthropic/claude-sonnet-4-6",
        quality: 3.9,
        quality_pass: false,
        doc_quality: { overall: 3.9, source_alignment: 3.5 },
        metrics_found: true,
        auto_verdict: null,
        auto_baseline_run_id: null,
      },
      delta: {
        doc_count: 0,
        quality: -0.6,
        cost_usd: 1,
        duration_ms: 200,
        input_tokens: 20,
        output_tokens: 10,
        num_turns: null,
      },
      delta_pct: {
        quality: -13.33,
        cost_usd: 100,
        duration_ms: 20,
        input_tokens: 20,
        output_tokens: 20,
        num_turns: null,
      },
      quality_delta: -0.6,
      quality_delta_pct: -13.33,
      verdict: "FAIL",
      checks: [],
    });

    renderWithClient(<ABComparison projectId="p1" />);
    const selects = await screen.findAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "1" } });
    fireEvent.change(selects[1], { target: { value: "2" } });

    expect(await screen.findByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("moonshot/kimi-k2.6")).toBeInTheDocument();
    expect(screen.getByText("anthropic/claude-sonnet-4-6")).toBeInTheDocument();
    expect(screen.getAllByText("source alignment").length).toBeGreaterThan(0);
  });

  it("shows validation aggregate and requires reviewer before submit", async () => {
    vi.mocked(api.listGenerateRuns).mockResolvedValue(runs);
    vi.mocked(api.getQuestionnaire).mockResolvedValue({
      project_id: "p1",
      run_id: 1,
      questions: [
        { id: "sections_present", question: "Sections present", score: 0 },
      ],
    });
    vi.mocked(api.getReviews).mockResolvedValue({
      project_id: "p1",
      run_id: 1,
      total_reviews: 2,
      average_overall: 8,
      average_per_question: { sections_present: 4.5 },
      pass_result: true,
      reviews: [],
    });

    renderWithClient(<ValidationReviewPanel projectId="p1" />);

    expect(await screen.findByText("Sections present")).toBeInTheDocument();
    expect(await screen.findByText("8.00")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Submit Review" }),
    ).toBeDisabled();
  });

  it("renders instruction pack metadata in project settings", async () => {
    const project: Project = {
      id: "p1",
      display_name: "Project",
      repo_url: null,
      local_path: "/tmp/p1",
      platform: "talend",
      doc_generation_provider: null,
      source_type: "zip",
      status: "registered",
      last_scan_at: null,
      last_generate_at: null,
      last_rag_push_at: null,
      job_count: 0,
      doc_count: 0,
      error_message: null,
      last_ingested_commit_sha: null,
      auto_reingest_enabled: true,
      auto_reingest_last_checked_at: null,
      auto_reingest_error: null,
      git_update_summary: null,
      has_github_token: false,
      connection_kind: "pat",
      github_account_login: null,
      webhook_status: null,
      instruction_pack_dir: null,
      plugin_pack_id: null,
      created_at: null,
      updated_at: null,
    };
    vi.mocked(api.listPlatforms).mockResolvedValue({ platforms: [] });
    vi.mocked(api.listModels).mockResolvedValue(neutralModels);
    vi.mocked(api.listPluginPacks).mockResolvedValue([]);
    vi.mocked(api.getProjectUsage).mockResolvedValue({
      project_id: "p1",
      total_generate_runs: 0,
      completed_generate_runs: 0,
      total_input_tokens: 0,
      total_output_tokens: 0,
      total_cost_usd: 0,
      total_scan_runs: 0,
      completed_scan_runs: 0,
    });
    vi.mocked(api.getInstructionPack).mockResolvedValue({
      path: "/repo/packs/default",
      relative_path: "packs/default",
      version: "v-test",
      exists: true,
      is_local: true,
      supported: true,
      preambles: [{ key: "phase2_preamble", bytes: 7, chars: 7 }],
    });

    renderWithClient(<ProjectSettings project={project} />);

    await waitFor(() =>
      expect(screen.getByText("Instruction Pack")).toBeInTheDocument(),
    );
    expect(await screen.findByText("v-test")).toBeInTheDocument();
    expect(screen.getByText("phase2_preamble (7 chars)")).toBeInTheDocument();
  });

  it("persists one selected plugin pack before generating through MCP", async () => {
    vi.mocked(api.listPluginPacks).mockResolvedValue([
      {
        id: 10, name: "Swap Pack", manifest_name: "Swap Pack", version: "2.0",
        description: "Talend migration documents", repo_url: null, repo_branch: null,
        last_commit_sha: null, auto_update: false, origin: "personal",
        certification_status: "approved", usable: true, mutable: false,
      },
    ]);
    vi.mocked(api.assignPluginPack).mockResolvedValue({ project_id: "p1", plugin_pack_id: 10 });
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });
    vi.mocked(api.generate).mockResolvedValue({
      status: "started",
      message: "ok",
    });

    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} />,
    );

    fireEvent.change(await screen.findByLabelText("Plugin pack"), { target: { value: "10" } });
    await waitFor(() => expect(api.assignPluginPack).toHaveBeenCalledWith("p1", 10));
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => {
      expect(api.generate).toHaveBeenCalledWith({
        projectId: "p1",
        resume: true,
        investigationModel: null,
        model: null,
        writeModel: null,
        generationMethod: "custom",
        liveEvidence: true,
      });
    });
  });

  it("does not offer unavailable plugin packs for selection", async () => {
    vi.mocked(api.listPluginPacks).mockResolvedValue([
      {
        id: 11, name: "Blocked Pack", manifest_name: "Blocked Pack", version: "1.0.0",
        description: null, repo_url: null, repo_branch: null, last_commit_sha: null,
        auto_update: false, origin: "shared", certification_status: "blocked",
        usable: false, mutable: false, blocked_reason: "Awaiting review",
      },
    ]);
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });

    renderWithClient(<GenerateDialog project={workspaceProject} open onClose={vi.fn()} />);

    expect(await screen.findByRole("option", { name: /none \(use the built-in/i })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Blocked Pack/ })).not.toBeInTheDocument();
  });

  it("uses one actual model for the complete OpenCode session", async () => {
    const openCodeProject = {
      ...workspaceProject,
      doc_generation_provider: "opencode-cli",
    };
    const descriptor = (id: string, label: string) => ({
      id,
      provider: id.split("/")[0],
      label,
      supports_analysis: true,
      supports_writing: true,
      is_default_analysis: id === "opencode-cli",
      is_default_investigation: false,
      is_default_writing: false,
      is_local: false,
      available: true,
      unavailable_reason: null,
      rag_capable: id !== "opencode-cli",
    });
    vi.mocked(api.listModels).mockResolvedValue({
      models: [
        descriptor("opencode-cli", "OpenCode CLI"),
        descriptor("openai/gpt-5.6-luna", "OpenAI GPT-5.6 Luna"),
        descriptor("openai/gpt-5.6-sol", "OpenAI GPT-5.6 Sol"),
        descriptor("openai/gpt-5.6-terra", "OpenAI GPT-5.6 Terra"),
        {
          ...descriptor("opencode-go/kimi-k3", "Kimi K3 — Go subscription"),
          available: false,
          unavailable_reason: "No credentials configured (set MCP_OPENCODE_GO_API_KEY)",
        },
      ],
    });
    vi.mocked(api.generate).mockResolvedValue({
      status: "started",
      message: "ok",
    });

    renderWithClient(
      <GenerateDialog project={openCodeProject} open onClose={vi.fn()} />,
    );

    expect(
      await screen.findByText("OpenCode Session Model"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Phase 1 — Investigation"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Phase 3 — Doc Writing")).not.toBeInTheDocument();
    await screen.findByRole("option", { name: /OpenAI GPT-5.6 Sol/ });
    const goOption = screen.getByRole("option", { name: /Kimi K3/ });
    expect(goOption).toBeDisabled();
    expect(goOption.parentElement).toHaveAttribute(
      "label",
      "OpenCode Go subscription",
    );
    const generateButton = await screen.findByRole("button", {
      name: "Generate",
    });
    expect(generateButton).toBeDisabled();

    fireEvent.change(
      screen.getByLabelText("Select OpenCode session model (required)"),
      { target: { value: "openai/gpt-5.6-sol" } },
    );
    fireEvent.click(generateButton);

    await waitFor(() => {
      expect(api.generate).toHaveBeenCalledWith({
        projectId: "p1",
        resume: true,
        investigationModel: null,
        model: "openai/gpt-5.6-sol",
        writeModel: null,
        generationMethod: "opencode-cli",
        // Sent on BOTH engines: OpenCode's `_prepare_run` calls the shared
        // runtime producer, so it honours this flag too.
        liveEvidence: true,
      });
    });
  });

  // THE GUARD. Two providers, neither of them one this build ships, rendered
  // from the catalogue alone. Each must appear with its OWN name and its OWN
  // type, and each must state which engine reads it -- all derived from
  // `kind`/`managed_by`, so a brand-new connector needs no change here.
  it("lists every live-data source generically, with its own name and type", async () => {
    const manageRuntimeEvidence = vi.fn();
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });
    vi.mocked(api.listConnectorCatalog).mockResolvedValue({
      providers: [inBandProvider, mirroredProvider],
    });
    vi.mocked(api.listConnectors).mockResolvedValue({ connectors: [inBandInstance], total: 1 });
    vi.mocked(api.listProjectConnectorBindings).mockResolvedValue({
      project_id: "p1",
      bindings: [inBandBinding],
    });
    vi.mocked(api.listRuntimeSources).mockResolvedValue({ sources: [mirroredSource], total: 1 });

    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} onManageRuntimeEvidence={manageRuntimeEvidence} />,
    );

    expect(await screen.findByText("Live data sources")).toBeInTheDocument();

    // Each row carries the provider's own identity and its own kind, in words.
    expect(await screen.findByText("Acme Telemetry (eu)")).toBeInTheDocument();
    expect(
      screen.getByText("Acme Telemetry · Live evidence - 2 governed read capabilities"),
    ).toBeInTheDocument();
    expect(screen.getByText("Connected · 3 sources")).toBeInTheDocument();

    expect(screen.getByText("Northwind Scheduler")).toBeInTheDocument();
    expect(screen.getByText("Runtime sync - mirrored on a schedule")).toBeInTheDocument();
    // Awaited: the mirrored lane's status is a query of its own, so it can
    // still be "Checking…" when the catalogue rows have already rendered.
    expect(await screen.findByText("Mirroring 1 source")).toBeInTheDocument();

    // No name of a shipped provider anywhere in the panel: the copy describes
    // whatever the catalogue served, not what this build happens to bundle.
    const panel = screen.getByText("Live data sources").closest("section")!;
    expect(panel.textContent).not.toMatch(/elastic/i);
    expect(panel.textContent).not.toMatch(/talend|TMC/i);

    // The mirrored lane's runtime store is asked about THIS project only.
    await waitFor(() =>
      expect(api.listRuntimeSources).toHaveBeenCalledWith({
        providerId: "northwind_scheduler",
        projectId: "p1",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Adjust connected systems" }));
    expect(manageRuntimeEvidence).toHaveBeenCalledWith("p1");
  });

  // "Which source am I actually using?" -- the question the old bare checkbox
  // could not answer, because it named a vendor and nothing else.
  it("names the mirrored source, its project and its last sync", async () => {
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });
    vi.mocked(api.listConnectorCatalog).mockResolvedValue({ providers: [mirroredProvider] });
    vi.mocked(api.listRuntimeSources).mockResolvedValue({ sources: [mirroredSource], total: 1 });

    renderWithClient(<GenerateDialog project={workspaceProject} open onClose={vi.fn()} />);

    const line = await screen.findByRole("listitem");
    expect(line).toHaveTextContent(
      `Northwind prod (eu-west) · p1 · last synced ${new Date(MIRROR_SYNCED_AT).toLocaleString()}`,
    );
  });

  // The lie this panel used to tell: an armed toggle for a project with no
  // mirrored source at all, promising runtime history that does not exist.
  it("arms no runtime toggle when the project has no mirrored source", async () => {
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });
    vi.mocked(api.listConnectorCatalog).mockResolvedValue({ providers: [mirroredProvider] });
    vi.mocked(api.listRuntimeSources).mockResolvedValue({ sources: [], total: 0 });

    renderWithClient(<GenerateDialog project={workspaceProject} open onClose={vi.fn()} />);

    expect(
      await screen.findByText(
        "No mirrored source is registered for p1, so this run has no Northwind Scheduler history to read.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Register one on the Connectors page to give this project runtime history."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: /mirrored runtime history/i }),
    ).not.toBeInTheDocument();
  });

  // Hard-won and still true: an in-band source is NOT read by the legacy
  // pipeline (the custom harness has no connector code path). It is now shown
  // in both modes and marked inert in the one that will not query it, rather
  // than silently vanishing -- and the reason is `kind`, not a provider id.
  it("marks an in-band source inert under the engine that will not query it", async () => {
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });
    vi.mocked(api.listConnectorCatalog).mockResolvedValue({
      providers: [inBandProvider, mirroredProvider],
    });
    vi.mocked(api.listConnectors).mockResolvedValue({ connectors: [inBandInstance], total: 1 });
    vi.mocked(api.listProjectConnectorBindings).mockResolvedValue({
      project_id: "p1",
      bindings: [inBandBinding],
    });
    vi.mocked(api.listRuntimeSources).mockResolvedValue({ sources: [mirroredSource], total: 1 });

    renderWithClient(<GenerateDialog project={workspaceProject} open onClose={vi.fn()} />);

    expect(
      await screen.findByText("Read by OpenCode runs; this run will not query it."),
    ).toBeInTheDocument();
    // The mirrored lane is read by both engines, in both modes.
    expect(
      screen.getAllByText("Read by both the legacy pipeline and OpenCode runs."),
    ).toHaveLength(1);

    fireEvent.click(screen.getByRole("tab", { name: "OpenCode" }));

    expect(screen.getByText("Read by OpenCode runs.")).toBeInTheDocument();
    expect(
      screen.queryByText("Read by OpenCode runs; this run will not query it."),
    ).not.toBeInTheDocument();
    // Still there under OpenCode: `_prepare_run` calls the same shared runtime
    // producer, so OpenCode honours `live_evidence` too.
    expect(
      screen.getByRole("checkbox", { name: /mirrored runtime history/i }),
    ).toBeInTheDocument();
  });

  it("labels the runtime opt-out narrowly and sends it on both engine paths", async () => {
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });
    vi.mocked(api.listConnectorCatalog).mockResolvedValue({ providers: [mirroredProvider] });
    vi.mocked(api.listRuntimeSources).mockResolvedValue({ sources: [mirroredSource], total: 1 });
    vi.mocked(api.generate).mockResolvedValue({ status: "started", message: "ok" });

    renderWithClient(<GenerateDialog project={workspaceProject} open onClose={vi.fn()} />);

    // Narrow label: the flag gates the mirrored runtime lane only. A generic
    // "live evidence off" would read as a kill switch for every connector while
    // an OpenCode run carried on querying its in-band sources.
    const checkbox = await screen.findByRole("checkbox", {
      name: /mirrored runtime history \(Northwind Scheduler\)/i,
    });
    expect(checkbox).toBeChecked();

    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(api.generate).toHaveBeenCalledWith(
      expect.objectContaining({ generationMethod: "custom", liveEvidence: false }),
    ));
  });

  it("does not block generation when the plugin catalogue is temporarily unavailable", async () => {
    vi.mocked(api.listPluginPacks).mockRejectedValue(new Error("Load failed"));
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });
    vi.mocked(api.generate).mockResolvedValue({ status: "started", message: "ok" });

    renderWithClient(<GenerateDialog project={workspaceProject} open onClose={vi.fn()} />);

    expect(await screen.findByText(/saved project pack will still be used/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await waitFor(() => expect(api.generate).toHaveBeenCalledWith({
      projectId: "p1", resume: true, investigationModel: null, model: null,
      writeModel: null, generationMethod: "custom", liveEvidence: true,
    }));
  });

  it("separates the OpenCode method from its session model", async () => {
    const descriptor = (
      id: string,
      label: string,
      isDefaultAnalysis = false,
    ) => ({
      id,
      provider: id.split("/")[0],
      label,
      supports_analysis: true,
      supports_writing: true,
      is_default_analysis: isDefaultAnalysis,
      is_default_investigation: isDefaultAnalysis,
      is_default_writing: isDefaultAnalysis,
      is_local: false,
      available: true,
      unavailable_reason: null,
      rag_capable: id !== "opencode-cli",
    });
    vi.mocked(api.listModels).mockResolvedValue({
      models: [
        descriptor("opencode-cli", "OpenCode CLI"),
        descriptor("openai/gpt-5.6-luna", "OpenAI GPT-5.6 Luna", true),
      ],
    });
    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} />,
    );

    const openCodeTab = await screen.findByRole("tab", { name: "OpenCode" });
    expect(
      screen.getByRole("tab", { name: "Legacy Pipeline" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      screen.queryByRole("option", { name: /OpenCode CLI/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(openCodeTab);

    expect(
      await screen.findByText("OpenCode Session Model"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Phase 1 — Investigation"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Phase 2 — Analysis")).not.toBeInTheDocument();
    expect(screen.queryByText("Phase 3 — Doc Writing")).not.toBeInTheDocument();
    expect(
      screen.getByLabelText("Select OpenCode session model (required)"),
    ).toBeInTheDocument();
    expect(openCodeTab).toHaveAttribute("aria-selected", "true");
  });
});
