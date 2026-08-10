import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import ABComparison from "../components/ABComparison";
import GenerateDialog from "../components/GenerateDialog";
import PluginPacksPage from "../components/PluginPacksPage";
import ProjectSettings from "../components/ProjectSettings";
import ValidationReviewPanel from "../components/ValidationReviewPanel";
import { api } from "../api/client";
import type { Project } from "../types";

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
    getPackWorkspace: vi.fn(),
    updatePackWorkspace: vi.fn(),
    generate: vi.fn(),
    generateOpenCodeCandidate: vi.fn(),
    getAgenticExperimentCapabilities: vi.fn(),
    listAgenticExperiments: vi.fn(),
  },
}));

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
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

const openCodeModels = {
  models: [{
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
  }],
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

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getAgenticExperimentCapabilities).mockResolvedValue({
    feature_enabled: true,
    provider_neutral: true,
    model_selection: "runtime_discovered",
    publishes: false,
    candidate_dispatch_available: true,
    paid_execution_enabled: true,
    opencode_version: "1.18.4",
    grounded_runtimes: [
      {
        runtime_id: "opencode",
        available: true,
        routes: [],
        paid_execution_confirmation_required: false,
        blockers: [],
      },
      {
        runtime_id: "openai-agents",
        available: true,
        routes: [
          { provider_id: "openai", model_id: "gpt-5.2", display_name: "GPT-5.2" },
          { provider_id: "deepseek", model_id: "deepseek-v4-flash", display_name: "DeepSeek V4 Flash" },
        ],
        paid_execution_confirmation_required: true,
        blockers: [],
      },
    ],
    supported_source_kinds: ["intodoc_scan"],
    recipes: [],
    execution_blockers: [],
  });
  vi.mocked(api.listAgenticExperiments).mockResolvedValue([]);
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
      delta: { doc_count: 0, quality: -0.6, cost_usd: 1, duration_ms: 200, input_tokens: 20, output_tokens: 10, num_turns: null },
      delta_pct: { quality: -13.33, cost_usd: 100, duration_ms: 20, input_tokens: 20, output_tokens: 20, num_turns: null },
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
      questions: [{ id: "sections_present", question: "Sections present", score: 0 }],
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
    expect(screen.getByRole("button", { name: "Submit Review" })).toBeDisabled();
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
    vi.mocked(api.listModels).mockResolvedValue(openCodeModels);
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

    await waitFor(() => expect(screen.getByText("Instruction Pack")).toBeInTheDocument());
    expect(await screen.findByText("v-test")).toBeInTheDocument();
    expect(screen.getByText("phase2_preamble (7 chars)")).toBeInTheDocument();
  });

  it("renders only plugin packs (no analysis add-ons) and saves the selection", async () => {
    const workspace = {
      project_id: "p1",
      organization_packs: [
        { id: 10, name: "Swap Pack", version: "2.0", description: "Base docs" },
      ],
      personal_packs: [
        {
          id: 21,
          name: "Quality Pack",
          version: "1.0",
          description: "Adds quality output",
          output_documents: ["quality.md"],
        },
      ],
      selected_organization_pack_id: 10,
      selected_private_version_ids: [],
    };
    vi.mocked(api.listProjects).mockResolvedValue({ projects: [workspaceProject], total: 1 });
    vi.mocked(api.getPackWorkspace).mockResolvedValue(workspace);
    vi.mocked(api.updatePackWorkspace).mockResolvedValue({
      ...workspace,
      selected_private_version_ids: [21],
    });

    renderWithClient(<PluginPacksPage isSuperadmin={false} />);

    expect(await screen.findByText("Plugin Packs")).toBeInTheDocument();
    expect(await screen.findByText("Swap Pack v2.0")).toBeInTheDocument();
    // Analysis (personal) packs are managed in the Nexus Workbench, not here.
    expect(screen.queryByText("Quality Pack v1.0")).not.toBeInTheDocument();
    expect(screen.queryByText("My add-ons")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/Advanced\/admin/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save pack selection" }));

    await waitFor(() => {
      // Existing private selections are preserved untouched (empty here).
      expect(api.updatePackWorkspace).toHaveBeenCalledWith("p1", 10, []);
    });
  });

  it("passes the selected workspace IDs from GenerateDialog to the Nexus hook", async () => {
    const workspace = {
      project_id: "p1",
      organization_packs: [
        { id: 10, name: "Swap Pack", version: "2.0", description: null },
      ],
      personal_packs: [
        { id: 21, name: "Quality Pack", version: "1.0", output_documents: [] },
      ],
      selected_organization_pack_id: 10,
      selected_private_version_ids: [21],
    };
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });
    vi.mocked(api.getPackWorkspace).mockResolvedValue(workspace);
    vi.mocked(api.generate).mockResolvedValue({ status: "started", message: "ok" });

    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} />,
    );

    expect(await screen.findByText("Swap Pack v2.0")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => {
      expect(api.generate).toHaveBeenCalledWith("p1", true, null, null, null, 10, [21]);
    });
  });

  it("starts an isolated single-agent OpenCode candidate from the normal Generate Docs dialog", async () => {
    vi.mocked(api.listModels).mockResolvedValue(openCodeModels);
    vi.mocked(api.getPackWorkspace).mockResolvedValue({
      project_id: "p1",
      organization_packs: [],
      personal_packs: [],
      selected_organization_pack_id: null,
      selected_private_version_ids: [],
    });
    vi.mocked(api.generateOpenCodeCandidate).mockResolvedValue({} as never);

    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByText("OpenCode single-agent"));
    expect(await screen.findByText(/Neutral Test Model/)).toBeInTheDocument();
    expect(screen.getAllByText(/never publishes or enters RAG/i)).toHaveLength(2);
    fireEvent.click(
      screen.getByRole("button", { name: "Start Single-Agent Candidate" }),
    );

    await waitFor(() => {
      expect(api.generateOpenCodeCandidate).toHaveBeenCalledWith(
        "p1",
        "single_agent",
        "provider-neutral-test",
        "provider-neutral-test/model-neutral-test",
        undefined,
        undefined,
        undefined,
      );
    });
    expect(api.generate).not.toHaveBeenCalled();
  });

  it("starts the orchestrated OpenCode candidate with specialist subagents", async () => {
    vi.mocked(api.listModels).mockResolvedValue(openCodeModels);
    vi.mocked(api.getPackWorkspace).mockResolvedValue({
      project_id: "p1",
      organization_packs: [],
      personal_packs: [],
      selected_organization_pack_id: null,
      selected_private_version_ids: [],
    });
    vi.mocked(api.generateOpenCodeCandidate).mockResolvedValue({} as never);

    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByText("OpenCode orchestrated"));
    expect(await screen.findByText(/Neutral Test Model/)).toBeInTheDocument();
    expect(screen.getAllByText(/bounded specialist subagents/i)).toHaveLength(2);
    fireEvent.click(
      screen.getByRole("button", { name: "Start Orchestrated Candidate" }),
    );

    await waitFor(() => {
      expect(api.generateOpenCodeCandidate).toHaveBeenCalledWith(
        "p1",
        "orchestrated",
        "provider-neutral-test",
        "provider-neutral-test/model-neutral-test",
        undefined,
        undefined,
        undefined,
      );
    });
    expect(api.generate).not.toHaveBeenCalled();
  });

  it("starts the recommended fact-grounded OpenCode candidate", async () => {
    vi.mocked(api.listModels).mockResolvedValue(openCodeModels);
    vi.mocked(api.getPackWorkspace).mockResolvedValue({
      project_id: "p1",
      organization_packs: [],
      personal_packs: [],
      selected_organization_pack_id: null,
      selected_private_version_ids: [],
    });
    vi.mocked(api.generateOpenCodeCandidate).mockResolvedValue({} as never);

    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByText("Nexus grounded — OpenCode"));
    expect(await screen.findByText(/Neutral Test Model/)).toBeInTheDocument();
    expect(screen.getByText(/Nexus to schedule a fact-grounded sequence/i)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Start Grounded Candidate" }),
    );

    await waitFor(() => {
      expect(api.generateOpenCodeCandidate).toHaveBeenCalledWith(
        "p1",
        "grounded",
        "provider-neutral-test",
        "provider-neutral-test/model-neutral-test",
        "opencode",
        undefined,
        undefined,
      );
    });
    expect(api.generate).not.toHaveBeenCalled();
  });

  it("offers to resume a previously failed Grounded attempt and passes its id through", async () => {
    vi.mocked(api.listModels).mockResolvedValue(openCodeModels);
    vi.mocked(api.getPackWorkspace).mockResolvedValue({
      project_id: "p1",
      organization_packs: [],
      personal_packs: [],
      selected_organization_pack_id: null,
      selected_private_version_ids: [],
    });
    vi.mocked(api.generateOpenCodeCandidate).mockResolvedValue({} as never);
    vi.mocked(api.listAgenticExperiments).mockResolvedValue([
      {
        experiment_id: "eea9b004-b693-471e-a865-2699c8e72ff7",
        project_id: "p1",
        baseline_run_id: null,
        status: "failed",
        comparison_status: "not_evaluated",
        candidate_verdict: "not_evaluated",
        runtime_id: "opencode-grounded",
        provider_id: "deepseek",
        model_id: "deepseek-v4-flash",
        source_snapshot_id: null,
        source_snapshot_frozen: false,
        recipe_snapshot_id: null,
        recipe_id: null,
        recipe_version: null,
        baseline_manifest_sha256: null,
        baseline_document_count: 0,
        artifact_count: 0,
        publishes: false,
        candidate_not_started: false,
        error_message: "grounded candidate output failed the secret-safety gate",
        baseline_metrics: {
          document_count: 0, duration_ms: null, provider_latency_ms: null,
          input_tokens: null, output_tokens: null, billed_cost_usd: null,
          quality_score: null, model_id: null, attempt_count: null, retry_count: null,
        },
        candidate_metrics: {
          document_count: 0, duration_ms: null, provider_latency_ms: null,
          input_tokens: null, output_tokens: null, billed_cost_usd: null,
          quality_score: null, model_id: null, attempt_count: 2, retry_count: 1,
        },
        safety_warnings: [],
        grounded_progress: {
          stage: "writing",
          accepted_artifact_count: 8,
          required_artifact_count: 10,
          roles: [],
        },
        artifacts: [],
      },
    ]);

    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByText("Nexus grounded — OpenCode"));
    expect(await screen.findByText(/Neutral Test Model/)).toBeInTheDocument();
    const resumeSelect = await screen.findByRole("combobox", {
      name: "Resume from a previous failed attempt",
    });
    expect(
      screen.getByText(/reached writing \(8\/10 artifacts accepted\)/),
    ).toBeInTheDocument();

    fireEvent.change(resumeSelect, {
      target: { value: "eea9b004-b693-471e-a865-2699c8e72ff7" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Start Grounded Candidate" }),
    );

    await waitFor(() => {
      expect(api.generateOpenCodeCandidate).toHaveBeenCalledWith(
        "p1",
        "grounded",
        "provider-neutral-test",
        "provider-neutral-test/model-neutral-test",
        "opencode",
        undefined,
        "eea9b004-b693-471e-a865-2699c8e72ff7",
      );
    });
  });

  it("starts the Nexus grounded architecture through OpenAI Agents only after paid confirmation", async () => {
    vi.mocked(api.listModels).mockResolvedValue(openCodeModels);
    vi.mocked(api.getPackWorkspace).mockResolvedValue({
      project_id: "p1",
      organization_packs: [],
      personal_packs: [],
      selected_organization_pack_id: null,
      selected_private_version_ids: [],
    });
    vi.mocked(api.generateOpenCodeCandidate).mockResolvedValue({} as never);

    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByText("Nexus grounded — OpenAI Agents"));
    const approvedModel = await screen.findByRole("combobox", { name: "Approved provider and model" });
    await waitFor(() => expect(approvedModel).toHaveValue(JSON.stringify(["openai", "gpt-5.2"])));
    const start = screen.getByRole("button", { name: "Start OpenAI Grounded Candidate" });
    expect(start).toBeDisabled();

    fireEvent.change(approvedModel, {
      target: { value: JSON.stringify(["deepseek", "deepseek-v4-flash"]) },
    });
    fireEvent.click(screen.getByText(/I understand this run uses the paid OpenAI API/i));
    expect(start).toBeEnabled();
    fireEvent.click(start);

    await waitFor(() => {
      expect(api.generateOpenCodeCandidate).toHaveBeenCalledWith(
        "p1",
        "grounded",
        "deepseek",
        "deepseek-v4-flash",
        "openai-agents",
        true,
        undefined,
      );
    });
    expect(api.generate).not.toHaveBeenCalled();
  });

  it("shows server blockers and prevents unavailable OpenAI Agents execution", async () => {
    vi.mocked(api.listModels).mockResolvedValue(openCodeModels);
    vi.mocked(api.getAgenticExperimentCapabilities).mockResolvedValue({
      feature_enabled: true,
      provider_neutral: true,
      model_selection: "runtime_discovered",
      publishes: false,
      candidate_dispatch_available: false,
      paid_execution_enabled: false,
      opencode_version: "1.18.4",
      grounded_runtimes: [{
        runtime_id: "openai-agents",
        available: false,
        routes: [{ provider_id: "openai", model_id: "gpt-5.2", display_name: "GPT-5.2" }],
        paid_execution_confirmation_required: true,
        blockers: ["OpenAI API credentials are unavailable"],
      }],
      supported_source_kinds: ["intodoc_scan"],
      recipes: [],
      execution_blockers: ["OpenAI API credentials are unavailable"],
    });

    renderWithClient(
      <GenerateDialog project={workspaceProject} open onClose={vi.fn()} />,
    );
    fireEvent.click(screen.getByText("Nexus grounded — OpenAI Agents"));

    expect(await screen.findByText("OpenAI API credentials are unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start OpenAI Grounded Candidate" })).toBeDisabled();
  });
});
