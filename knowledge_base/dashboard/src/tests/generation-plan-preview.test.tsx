import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import GenerateDialog from "../components/GenerateDialog";
import { api } from "../api/client";
import type { GenerationPlan, Project } from "../types";

vi.mock("../api/client", () => ({
  api: {
    getGenerationPlan: vi.fn(),
    generate: vi.fn(),
    listModels: vi.fn(),
    listPluginPacks: vi.fn(),
    assignPluginPack: vi.fn(),
    listConnectorCatalog: vi.fn(),
    listConnectors: vi.fn(),
    listProjectConnectorBindings: vi.fn(),
    listRuntimeSources: vi.fn(),
    getRuntimeReadScope: vi.fn(),
  },
}));

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const project: Project = {
  id: "werkorder",
  display_name: "Werkorder",
  repo_url: null,
  local_path: "/tmp/werkorder",
  platform: "talend",
  doc_generation_provider: null,
  source_type: "zip",
  status: "ready",
  last_scan_at: null,
  last_generate_at: null,
  last_rag_push_at: null,
  job_count: 429,
  doc_count: 12,
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

const COMPOSITE_PLAN: GenerationPlan = {
  project_id: "werkorder",
  scan_available: true,
  composite: true,
  partition_count: 3,
  will_generate: 1,
  will_reuse: 2,
  global_invalidation: null,
  partitions: [
    {
      name: "werkorder_archive",
      subdir: "werkorder_archive",
      action: "generate",
      reason: "new",
      unit_count: 34,
      doc_count: 0,
      last_generated_at: null,
    },
    {
      name: "werkorder_core",
      subdir: "werkorder_core",
      action: "reuse",
      reason: "unchanged",
      unit_count: 120,
      doc_count: 11,
      last_generated_at: "2026-08-20T09:15:00Z",
    },
    {
      name: "werkorder_reporting",
      subdir: "werkorder_reporting",
      action: "reuse",
      reason: "unchanged",
      unit_count: 40,
      doc_count: 6,
      last_generated_at: "2026-08-20T09:40:00Z",
    },
  ],
  notes: [
    "Cross-project references inside reused sub-projects are not rewritten. The landscape topology document is always rebuilt.",
  ],
};

/**
 * Match a whole rendered line, not a text node.
 *
 * Each detail line mixes plain text with the reason code in its own `<span>` so
 * the code renders monospaced, which means the line is several text nodes and a
 * plain string or regex matcher never sees it as one string.
 */
function line(pattern: RegExp) {
  return (_content: string, element: Element | null) =>
    element?.tagName === "P" &&
    element.children.length > 0 &&
    pattern.test(element.textContent ?? "");
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listModels).mockResolvedValue({ models: [] });
  vi.mocked(api.listPluginPacks).mockResolvedValue([]);
  vi.mocked(api.listConnectorCatalog).mockResolvedValue({ providers: [] });
  vi.mocked(api.listConnectors).mockResolvedValue({ connectors: [], total: 0 });
  vi.mocked(api.listProjectConnectorBindings).mockResolvedValue({
    project_id: "werkorder",
    bindings: [],
  });
  vi.mocked(api.listRuntimeSources).mockResolvedValue({ sources: [], total: 0 });
  vi.mocked(api.generate).mockResolvedValue({ status: "started", message: "ok" });
});

describe("GenerateDialog generation plan preview", () => {
  it("hides partitioning and promises one whole-estate session for OpenCode", async () => {
    vi.mocked(api.getGenerationPlan).mockResolvedValue(COMPOSITE_PLAN);

    renderWithClient(
      <GenerateDialog
        project={{ ...project, doc_generation_provider: "opencode-cli" }}
        open
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText("Whole-estate generation")).toBeInTheDocument();
    expect(screen.getByText(/complete scanned estate in one workspace/)).toBeInTheDocument();
    expect(screen.queryByText("1 of 3")).not.toBeInTheDocument();
    expect(screen.queryByText("Will generate (1)")).not.toBeInTheDocument();
  });

  it("shows the generate/reuse split with a reason per sub-project", async () => {
    vi.mocked(api.getGenerationPlan).mockResolvedValue(COMPOSITE_PLAN);

    renderWithClient(<GenerateDialog project={project} open onClose={vi.fn()} />);

    // The headline is the cost sentence: 1 of 3, not "some of them".
    expect(await screen.findByText("1 of 3")).toBeInTheDocument();
    expect(screen.getByText(/2 reused at no model cost/)).toBeInTheDocument();

    expect(screen.getByText("Will generate (1)")).toBeInTheDocument();
    expect(screen.getByText("Will be reused (2)")).toBeInTheDocument();

    expect(screen.getByText("werkorder_archive")).toBeInTheDocument();
    expect(screen.getByText(line(/^34 units · new$/))).toBeInTheDocument();
    expect(screen.getByText("werkorder_core")).toBeInTheDocument();
    expect(screen.getByText(line(/^11 docs · .+ · unchanged$/))).toBeInTheDocument();

    // Every note is rendered; they carry the limits of a partial run.
    expect(
      screen.getByText(/The landscape topology document is always rebuilt/),
    ).toBeInTheDocument();

    // And the button says what it will do.
    expect(
      screen.getByRole("button", { name: "Generate 1 sub-project" }),
    ).toBeInTheDocument();
  });

  it("states the reason when everything regenerates anyway", async () => {
    vi.mocked(api.getGenerationPlan).mockResolvedValue({
      ...COMPOSITE_PLAN,
      will_generate: 3,
      will_reuse: 0,
      global_invalidation: {
        reason: "shared_routine_changed",
        detail:
          "A shared Talend routine changed. Routines carry no source-root attribution, so every sub-project must be re-read.",
      },
      partitions: COMPOSITE_PLAN.partitions.map((partition) => ({
        ...partition,
        action: "generate",
        reason: "shared_artifact_changed",
      })),
    });

    renderWithClient(<GenerateDialog project={project} open onClose={vi.fn()} />);

    expect(
      await screen.findByText(/Routines carry no source-root attribution/),
    ).toBeInTheDocument();
    // The raw code too: this is the operator console, and the code is what a
    // support conversation can be held about.
    expect(screen.getByText("shared_routine_changed")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Generate 3 sub-projects" }),
    ).toBeInTheDocument();
  });

  it("hides the preview for a single-run project", async () => {
    vi.mocked(api.getGenerationPlan).mockResolvedValue({
      project_id: "solo",
      scan_available: true,
      composite: false,
      partition_count: 0,
      will_generate: 1,
      will_reuse: 0,
      global_invalidation: null,
      partitions: [],
      notes: [],
    });

    renderWithClient(<GenerateDialog project={project} open onClose={vi.fn()} />);

    // Today's dialog, unchanged: no plan block, no count in the button.
    expect(await screen.findByRole("button", { name: "Generate" })).toBeInTheDocument();
    expect(screen.queryByText("Generation plan")).not.toBeInTheDocument();
  });

  it("hides the preview when the project has not been scanned", async () => {
    vi.mocked(api.getGenerationPlan).mockResolvedValue({
      ...COMPOSITE_PLAN,
      scan_available: false,
      partition_count: 0,
      will_generate: 0,
      will_reuse: 0,
      partitions: [],
      notes: [],
    });

    renderWithClient(<GenerateDialog project={project} open onClose={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "Generate" })).toBeInTheDocument();
    expect(screen.queryByText("Generation plan")).not.toBeInTheDocument();
  });

  it("still generates when the plan cannot be read", async () => {
    // The plan describes what the run will do; the run does not need it. A
    // failed preview must never be a gate.
    vi.mocked(api.getGenerationPlan).mockRejectedValue(new Error("500: boom"));

    renderWithClient(<GenerateDialog project={project} open onClose={vi.fn()} />);

    expect(
      await screen.findByText(/The generation plan could not be read/),
    ).toBeInTheDocument();

    const button = screen.getByRole("button", { name: "Generate" });
    expect(button).not.toBeDisabled();
    fireEvent.click(button);

    await waitFor(() =>
      expect(api.generate).toHaveBeenCalledWith({
        projectId: "werkorder",
        resume: true,
        investigationModel: null,
        model: null,
        writeModel: null,
        generationMethod: "custom",
        liveEvidence: true,
      }),
    );
  });

  it("retries a failed plan without reopening the dialog", async () => {
    vi.mocked(api.getGenerationPlan)
      .mockRejectedValueOnce(new Error("500: boom"))
      .mockResolvedValueOnce(COMPOSITE_PLAN);

    renderWithClient(<GenerateDialog project={project} open onClose={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

    expect(await screen.findByText("1 of 3")).toBeInTheDocument();
  });
});
