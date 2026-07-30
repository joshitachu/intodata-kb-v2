import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgenticExperimentsPage from "../components/AgenticExperimentsPage";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    listProjects: vi.fn(),
    getAgenticExperimentCapabilities: vi.fn(),
    listAgenticExperiments: vi.fn(),
    getAgenticExperiment: vi.fn(),
    listAgenticExperimentTasks: vi.fn(),
    createAgenticExperiment: vi.fn(),
    freezeAgenticSourceSnapshot: vi.fn(),
    compileAgenticRecipe: vi.fn(),
    retryAgenticExperimentTask: vi.fn(),
    reconcileAgenticExperiment: vi.fn(),
    quarantineAgenticExperiment: vi.fn(),
    restoreAgenticExperiment: vi.fn(),
  },
}));

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const project = {
  id: "alitalend",
  display_name: "AliTalend",
  repo_url: null,
  local_path: "/data/alitalend",
  platform: "talend",
  doc_generation_provider: null,
  source_type: "git",
  status: "ready",
  last_scan_at: null,
  last_generate_at: null,
  last_rag_push_at: null,
  job_count: 10,
  doc_count: 10,
  error_message: null,
  last_ingested_commit_sha: null,
  auto_reingest_enabled: false,
  auto_reingest_last_checked_at: null,
  auto_reingest_error: null,
  git_update_summary: null,
  has_github_token: false,
  connection_kind: "pat" as const,
  github_account_login: null,
  webhook_status: null,
  instruction_pack_dir: null,
  plugin_pack_id: null,
  created_at: null,
  updated_at: null,
};

function makeExperiment() {
  return {
  experiment_id: "exp-1",
  project_id: "alitalend",
  baseline_run_id: 44,
  status: "queued",
  comparison_status: "not_evaluated",
  candidate_verdict: "not_evaluated",
  runtime_id: "fake",
  provider_id: null,
  model_id: null,
  source_snapshot_id: null as string | null,
  source_snapshot_frozen: false,
  recipe_snapshot_id: null as string | null,
  recipe_id: null as string | null,
  recipe_version: null as string | null,
  baseline_manifest_sha256: "a".repeat(64),
  baseline_document_count: 10,
  artifact_count: 1,
  publishes: false as const,
  candidate_not_started: true,
  error_message: null,
  baseline_metrics: {
    document_count: 10,
    duration_ms: 1_438_756,
    provider_latency_ms: null,
    input_tokens: 1_008_625,
    output_tokens: 132_891,
    billed_cost_usd: null,
    quality_score: 3.84,
    model_id: "legacy-model",
    attempt_count: null,
    retry_count: null,
  },
  candidate_metrics: {
    document_count: 0,
    duration_ms: null,
    provider_latency_ms: null,
    input_tokens: null,
    output_tokens: null,
    billed_cost_usd: null,
    quality_score: null,
    model_id: null,
    attempt_count: 0,
    retry_count: 0,
  },
  safety_warnings: [],
  grounded_progress: null as null | {
    stage: string;
    accepted_artifact_count: number;
    required_artifact_count: number;
    roles: {
      role_id: string;
      stage: string;
      status: "pending" | "completed";
      accepted_artifact_count: number;
      owned_artifact_count: number;
    }[];
  },
  artifacts: [{
    branch: "baseline",
    kind: "document",
    logical_name: "09_EXECUTIVE_SUMMARY.md",
    relative_path: "baseline/doc.md",
    sha256: "b".repeat(64),
    media_type: "text/markdown",
    size_bytes: 120,
  }],
  };
}

let experiment = makeExperiment();

beforeEach(() => {
  vi.resetAllMocks();
  experiment = makeExperiment();
  vi.mocked(api.listProjects).mockResolvedValue({ projects: [project], total: 1 });
  vi.mocked(api.getAgenticExperimentCapabilities).mockResolvedValue({
    feature_enabled: true,
    provider_neutral: true,
    model_selection: "runtime_discovered",
    publishes: false,
    candidate_dispatch_available: false,
    paid_execution_enabled: false,
    opencode_version: "1.18.4",
    supported_source_kinds: [
      "intodoc_scan",
      "directory_evidence",
      "tabular_file",
      "runtime_export",
    ],
    recipes: [
      {
        recipe_id: "alitalend-legacy-equivalence",
        recipe_version: "1.0.0",
        source_requirements: [{
          source_role: "implementation",
          required_capabilities: ["searchable_text", "structured_records"],
          optional: false,
        }],
        required_model_capabilities: ["tools"],
        output_ids: ["09_EXECUTIVE_SUMMARY"],
        document_output_count: 10,
      },
      {
        recipe_id: "logistics-operations",
        recipe_version: "1.0.0",
        source_requirements: [
          {
            source_role: "operational_policy",
            required_capabilities: ["searchable_text"],
            optional: false,
          },
          {
            source_role: "shipment_events",
            required_capabilities: ["structured_records", "tabular_data"],
            optional: false,
          },
        ],
        required_model_capabilities: ["tools"],
        output_ids: ["operations_runbook", "exception_inventory"],
        document_output_count: 2,
      },
    ],
    execution_blockers: [
      "Candidate dispatch is not exposed by this API",
      "Paid execution is disabled",
    ],
  });
  vi.mocked(api.listAgenticExperiments).mockResolvedValue([experiment]);
  vi.mocked(api.getAgenticExperiment).mockResolvedValue(experiment);
  vi.mocked(api.listAgenticExperimentTasks).mockResolvedValue({ experiment_id: "exp-1", tasks: [], total: 0, publishes: false });
  vi.mocked(api.createAgenticExperiment).mockResolvedValue(experiment);
  vi.mocked(api.freezeAgenticSourceSnapshot).mockImplementation(async () => {
    experiment.source_snapshot_id = "scan-1";
    experiment.source_snapshot_frozen = true;
    return {
      experiment_id: "exp-1", snapshot_id: "scan-1", manifest_sha256: "c".repeat(64), security_state: "approved", manifest_relative_path: "shared/scan.json", item_count: 10, source_kinds: ["intodoc_scan"], source_roles: ["implementation"], publishes: false,
    };
  });
  vi.mocked(api.compileAgenticRecipe).mockResolvedValue({
    experiment_id: "exp-1", snapshot_id: "recipe-1", recipe_id: "alitalend-legacy-equivalence", recipe_version: "1.0.0", graph_sha256: "d".repeat(64), effective_scope_sha256: "e".repeat(64), manifest_relative_path: "shared/recipe.json", task_count: 12, document_output_count: 10, preflight_required: true, publishes: false,
  });
});

describe("AgenticExperimentsPage", () => {
  it("shows grounded file-backed stage and role progress without source contents", async () => {
    experiment.runtime_id = "opencode-grounded";
    experiment.status = "running";
    experiment.grounded_progress = {
      stage: "writing",
      accepted_artifact_count: 8,
      required_artifact_count: 17,
      roles: [
        {
          role_id: "writer-foundation",
          stage: "writing",
          status: "pending",
          accepted_artifact_count: 3,
          owned_artifact_count: 5,
        },
      ],
    };

    renderWithClient(<AgenticExperimentsPage />);

    expect(await screen.findByText("Grounded stage: writing")).toBeInTheDocument();
    expect(screen.getByText("8/17 artifacts")).toBeInTheDocument();
    expect(screen.getByText("writer-foundation")).toBeInTheDocument();
    expect(screen.getByText(/reasoning and source contents are not exposed/i)).toBeInTheDocument();
  });

  it("requires non-publishing acknowledgement before it can freeze a latest baseline", async () => {
    renderWithClient(<AgenticExperimentsPage />);

    await screen.findByRole("option", { name: /AliTalend/ });
    const create = screen.getByRole("button", { name: "Create & freeze latest baseline" });
    expect(create).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Acknowledge non-publishing experiment"));
    expect(create).toBeEnabled();
    fireEvent.click(create);

    await waitFor(() => expect(api.createAgenticExperiment).toHaveBeenCalledWith(expect.objectContaining({
      project_id: "alitalend",
      acknowledge_non_publishing: true,
      runtime_id: "opencode",
      recipe_id: "alitalend-legacy-equivalence",
    })));
    expect(screen.getByText(/Candidate execution is unavailable/)).toBeInTheDocument();
  });

  it("freezes the current IntoDoc scan before allowing an AliTalend recipe compile", async () => {
    renderWithClient(<AgenticExperimentsPage />);

    await screen.findByText("Experiment exp-1");
    const compile = screen.getByRole("button", { name: "2. Compile recipe" });
    expect(compile).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "1. Freeze sources" }));
    await waitFor(() => expect(api.freezeAgenticSourceSnapshot).toHaveBeenCalledWith(
      "exp-1",
      [expect.objectContaining({
        source_kind: "intodoc_scan",
        source_role: "implementation",
      })],
    ));
    await waitFor(() => expect(compile).toBeEnabled());
    fireEvent.click(compile);
    await waitFor(() => expect(api.compileAgenticRecipe).toHaveBeenCalledWith("exp-1", expect.objectContaining({
      recipe_id: "alitalend-legacy-equivalence", model_capabilities: ["tools"],
    })));
    expect(screen.getByText("Candidate artifacts")).toBeInTheDocument();
    expect(screen.getByText(/candidate execution has not started/)).toBeInTheDocument();
  });

  it("composes non-code logistics inputs from the backend recipe contract", async () => {
    renderWithClient(<AgenticExperimentsPage />);

    await screen.findByText("Experiment exp-1");
    fireEvent.change(screen.getByLabelText("Agentic recipe"), {
      target: { value: "logistics-operations" },
    });
    const paths = await screen.findAllByPlaceholderText(
      "folder/file relative to governed root 0",
    );
    expect(paths).toHaveLength(2);
    fireEvent.change(paths[0], { target: { value: "policies/" } });
    fireEvent.change(paths[1], { target: { value: "exports/shipments.xlsx" } });
    fireEvent.click(screen.getByRole("button", { name: "1. Freeze sources" }));

    await waitFor(() => expect(api.freezeAgenticSourceSnapshot).toHaveBeenCalledWith(
      "exp-1",
      [
        expect.objectContaining({
          source_role: "operational_policy",
          source_kind: "directory_evidence",
          relative_path: "policies/",
        }),
        expect.objectContaining({
          source_role: "shipment_events",
          source_kind: "tabular_file",
          relative_path: "exports/shipments.xlsx",
        }),
      ],
    ));
  });
});
