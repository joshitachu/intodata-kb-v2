import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GeneratePackDialog } from "@/app/settings/sources/GeneratePackDialog";
import { getGenerationPlan } from "@/lib/mcpClient";
import { listPacks } from "@/lib/packs";
import type { GenerationPlan } from "@/types";

vi.mock("@/lib/mcpClient", async () => {
  const actual = await vi.importActual<typeof import("@/lib/mcpClient")>("@/lib/mcpClient");
  return { ...actual, getGenerationPlan: vi.fn() };
});

vi.mock("@/lib/packs", async () => {
  const actual = await vi.importActual<typeof import("@/lib/packs")>("@/lib/packs");
  return { ...actual, listPacks: vi.fn(), selectProjectPack: vi.fn() };
});

/**
 * The preview exists for cost honesty: an operator must see "1 of 6
 * sub-projects will regenerate" before committing, and must see the stated
 * reason when all 6 regenerate anyway. It must also never be able to stop them
 * generating -- it is advisory, and the run is the thing they came for.
 */
const COMPOSITE_PLAN: GenerationPlan = {
  project_id: "werkorder",
  scan_available: true,
  composite: true,
  partition_count: 6,
  will_generate: 1,
  will_reuse: 5,
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
      doc_count: 41,
      last_generated_at: "2026-08-12T10:00:00Z",
    },
  ],
  notes: [
    "Cross-project references inside reused sub-projects are not rewritten. The landscape topology document is always rebuilt.",
  ],
};

function open(plan?: GenerationPlan) {
  if (plan) vi.mocked(getGenerationPlan).mockResolvedValue(plan);
  return render(
    <GeneratePackDialog
      open
      projectId="werkorder"
      projectLabel="Werkorder"
      currentPackId={null}
      onCancel={() => undefined}
      onConfirm={() => undefined}
    />,
  );
}

describe("GeneratePackDialog generation plan preview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listPacks).mockResolvedValue([]);
  });

  it("shows the generate/reuse split and reflects the count on the button", async () => {
    open(COMPOSITE_PLAN);

    await waitFor(() =>
      expect(
        screen.getByText("1 of 6 sub-projects will be regenerated"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/5 reused/)).toBeInTheDocument();
    // Named, not just counted: the operator has to be able to check that the
    // sub-project they changed is on the generate side.
    expect(screen.getByText("werkorder_archive")).toBeInTheDocument();
    expect(screen.getByText(/34 units · new sub-project/)).toBeInTheDocument();
    expect(screen.getByText("werkorder_core")).toBeInTheDocument();
    expect(screen.getByText(/41 documents · generated/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Generate 1 sub-project$/ }),
    ).toBeInTheDocument();
  });

  it("renders every note the server returned", async () => {
    open(COMPOSITE_PLAN);

    await waitFor(() =>
      expect(
        screen.getByText(/Cross-project references inside reused sub-projects/),
      ).toBeInTheDocument(),
    );
  });

  it("states why nothing can be reused when the whole project is invalidated", async () => {
    open({
      ...COMPOSITE_PLAN,
      will_generate: 6,
      will_reuse: 0,
      global_invalidation: {
        reason: "shared_routine_changed",
        detail:
          "A shared Talend routine changed. Routines carry no source-root attribution, so every sub-project must be re-read.",
      },
      partitions: COMPOSITE_PLAN.partitions.map((part) => ({
        ...part,
        action: "generate" as const,
        reason: "shared_artifact_changed",
      })),
    });

    await waitFor(() =>
      expect(screen.getByText(/A shared Talend routine changed/)).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: /Generate 6 sub-projects/ }),
    ).toBeInTheDocument();
  });

  it("hides the preview for a single-run project", async () => {
    open({
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

    await waitFor(() => expect(getGenerationPlan).toHaveBeenCalledWith("werkorder"));
    expect(screen.queryByText(/will be regenerated/)).not.toBeInTheDocument();
    // Today's dialog, unchanged.
    expect(screen.getByRole("button", { name: /Start generating/ })).toBeInTheDocument();
  });

  it("hides the preview when there is no scan yet", async () => {
    open({
      project_id: "werkorder",
      scan_available: false,
      composite: true,
      partition_count: 0,
      will_generate: 0,
      will_reuse: 0,
      global_invalidation: null,
      partitions: [],
      notes: [],
    });

    await waitFor(() => expect(getGenerationPlan).toHaveBeenCalled());
    expect(screen.queryByText(/will be regenerated/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start generating/ })).toBeInTheDocument();
  });

  it("still allows generating when the plan cannot be fetched", async () => {
    vi.mocked(getGenerationPlan).mockRejectedValue(new Error("plan endpoint down"));
    open();

    await waitFor(() =>
      expect(screen.getByText(/the cost preview is not shown/)).toBeInTheDocument(),
    );
    const button = screen.getByRole("button", { name: /Start generating/ });
    // The whole point: a missing estimate is not a blocked run.
    expect(button).not.toBeDisabled();
    expect(screen.queryByText(/will be regenerated/)).not.toBeInTheDocument();
  });
});
