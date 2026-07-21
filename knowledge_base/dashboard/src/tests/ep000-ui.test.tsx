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
    vi.mocked(api.listModels).mockResolvedValue({ models: [] });
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

  it("renders the project pack workspace and saves unified selections", async () => {
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

    expect(await screen.findByText("Documentation Packs")).toBeInTheDocument();
    expect(await screen.findByText("Swap Pack v2.0")).toBeInTheDocument();
    expect(screen.getByText("Quality Pack v1.0")).toBeInTheDocument();
    expect(screen.queryByText(/Advanced\/admin/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Save pack selection" }));

    await waitFor(() => {
      expect(api.updatePackWorkspace).toHaveBeenCalledWith("p1", 10, [21]);
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
});
