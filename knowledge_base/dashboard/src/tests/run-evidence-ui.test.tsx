import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RunHistory from "../components/RunHistory";
import type { GenerateRun, RunRuntimeEvidenceSummary } from "../types";

const hooks = vi.hoisted(() => ({
  runs: vi.fn(),
  evidence: vi.fn(),
}));

vi.mock("../hooks/useProjects", () => ({ useGenerateRuns: hooks.runs }));
vi.mock("../hooks/useConnectors", () => ({ useRunConnectorEvidence: hooks.evidence }));

const run: GenerateRun = {
  id: 51,
  project_id: "werkorder",
  triggered_by: "api",
  status: "completed",
  started_at: "2026-08-20T09:00:00Z",
  completed_at: "2026-08-20T09:30:00Z",
  cost_usd: null,
  duration_ms: null,
  duration_api_ms: null,
  input_tokens: null,
  output_tokens: null,
  num_turns: null,
  session_id: null,
  doc_count: 12,
  error_message: null,
  log_file: null,
  model_name: null,
  write_model_name: null,
  quality_score: null,
  auto_verdict: null,
  auto_baseline_run_id: null,
  run_verdict: null,
  gate_results: null,
};

function runtime(overrides: Partial<RunRuntimeEvidenceSummary> = {}): RunRuntimeEvidenceSummary {
  return {
    available: true,
    mode: "data",
    window_days: 90,
    units_requested: 374,
    units_matched: 5,
    orphan_runtime_job_count: 0,
    partial: false,
    scope_mismatch: false,
    batches: 2,
    source_key: "tmc",
    subproject: null,
    subproject_count: 0,
    ...overrides,
  };
}

function showEvidence(
  summary: RunRuntimeEvidenceSummary,
  subprojects: RunRuntimeEvidenceSummary[] = [],
) {
  hooks.evidence.mockReturnValue({
    data: {
      run_id: run.id,
      available: false,
      connector_count: 0,
      successful_calls: 0,
      failed_calls: 0,
      denied_calls: 0,
      truncated_calls: 0,
      observation_from: null,
      observation_to: null,
      limitations: [],
      findings: [],
      runtime: summary,
      runtime_subprojects: subprojects,
    },
    isLoading: false,
    isError: false,
  });
  render(<RunHistory projectId="werkorder" onViewLog={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Evidence" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  hooks.runs.mockReturnValue({ data: { runs: [run], total: 1 }, isLoading: false });
});

describe("run evidence popover", () => {
  it("states runtime coverage with its window", () => {
    showEvidence(runtime());

    expect(screen.getByText("Runtime (TMC): 5/374 units matched - 90-day window")).toBeInTheDocument();
  });

  it("says a low match count is expected, and raises nothing on a healthy run", () => {
    // 5 of 374 is the normal shape: the runtime source lists scheduled tasks,
    // and most scanned units are child jobs nothing schedules directly. A
    // warning here would be a confident false alarm.
    showEvidence(runtime({ units_matched: 5, units_requested: 374, orphan_runtime_job_count: 0 }));

    expect(
      screen.getByText(
        "Unmatched units are expected: the runtime source lists scheduled tasks, not every scanned unit.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no scanned counterpart/)).not.toBeInTheDocument();
    expect(screen.queryByText(/does not contain/)).not.toBeInTheDocument();
  });

  it("reads scope_mismatch from the payload rather than inferring it", () => {
    // Reported by the backend against its own orphan-ratio threshold, and true
    // here despite a non-zero match count -- which the old inference missed.
    showEvidence(
      runtime({ units_matched: 3, orphan_runtime_job_count: 28, scope_mismatch: true }),
    );

    expect(
      screen.getByText(
        "Runtime (TMC): 3/374 units matched. 28 runtime jobs have no scanned counterpart - the runtime source lists jobs this scan does not contain.",
      ),
    ).toBeInTheDocument();
  });

  it("stays silent when the payload reports scope_mismatch false", () => {
    // An explicit denial outranks the legacy guess, even in its exact shape
    // (zero matched, orphans present).
    showEvidence(
      runtime({ units_matched: 0, orphan_runtime_job_count: 4, scope_mismatch: false }),
    );

    expect(screen.queryByText(/no scanned counterpart/)).not.toBeInTheDocument();
  });

  it("falls back to inference for an artifact predating scope_mismatch", () => {
    showEvidence(
      runtime({ units_matched: 0, orphan_runtime_job_count: 1, scope_mismatch: null }),
    );

    expect(
      screen.getByText(
        "Runtime (TMC): 0/374 units matched. 1 runtime job has no scanned counterpart - the runtime source lists jobs this scan does not contain.",
      ),
    ).toBeInTheDocument();
  });

  it("reuses the skipped-or-unavailable line for an opted-out run", () => {
    showEvidence(runtime({ mode: "opted_out", units_matched: 0, window_days: null }));

    expect(
      screen.getByText("Runtime evidence was skipped or unavailable for this run."),
    ).toBeInTheDocument();
  });

  it("says the same for a run that predates the coverage artifact", () => {
    showEvidence({
      available: false, mode: "unavailable", window_days: null, units_requested: 0,
      units_matched: 0, orphan_runtime_job_count: 0, partial: false, scope_mismatch: null,
      batches: null, source_key: null, subproject: null, subproject_count: 0,
    });

    expect(
      screen.getByText("Runtime evidence was skipped or unavailable for this run."),
    ).toBeInTheDocument();
    // No scope caveat when there is no figure to caveat.
    expect(screen.queryByText(/latest published docs/)).not.toBeInTheDocument();
  });

  it("flags a partial fetch without hiding the coverage it did get", () => {
    showEvidence(runtime({ mode: "partial", partial: true }));

    expect(screen.getByText("Runtime (TMC): 5/374 units matched - 90-day window")).toBeInTheDocument();
    expect(screen.getByText("Coverage is partial: not every batch was retrieved.")).toBeInTheDocument();
  });

  it("says plainly that the figure describes the published docs, not this run", () => {
    showEvidence(runtime());

    expect(
      screen.getByText("Describes the latest published docs for this project, not this run."),
    ).toBeInTheDocument();
  });

  it("names the sub-project a composite child run describes", () => {
    showEvidence(runtime({ subproject: "vise2assetcheck", units_matched: 12, units_requested: 40 }));

    expect(screen.getByText("Describes sub-project vise2assetcheck.")).toBeInTheDocument();
    // A child run reports itself, so there is no breakdown to show.
    expect(screen.queryByText(/Aggregate across/)).not.toBeInTheDocument();
  });

  it("marks an aggregate as such and breaks it down per sub-project", () => {
    showEvidence(
      runtime({ units_matched: 17, units_requested: 80, subproject_count: 3 }),
      [
        runtime({ subproject: "vise2assetcheck", units_matched: 12, units_requested: 40 }),
        runtime({ subproject: "werkorder", units_matched: 5, units_requested: 40 }),
        { ...runtime(), subproject: "reversengineer", available: false, mode: "unavailable" },
      ],
    );

    expect(screen.getByText("Aggregate across 3 sub-projects.")).toBeInTheDocument();
    expect(screen.getByText("vise2assetcheck: 12/40 units matched")).toBeInTheDocument();
    expect(screen.getByText("werkorder: 5/40 units matched")).toBeInTheDocument();
    expect(screen.getByText("reversengineer: no coverage artifact")).toBeInTheDocument();
    // The headline stays the whole upload, not one arbitrary sub-project.
    expect(screen.getByText("Runtime (TMC): 17/80 units matched - 90-day window")).toBeInTheDocument();
  });

  it("shows the aggregate sentence even when no breakdown rows were sent", () => {
    showEvidence(runtime({ subproject_count: 2 }), []);

    expect(screen.getByText("Aggregate across 2 sub-projects.")).toBeInTheDocument();
  });
});
