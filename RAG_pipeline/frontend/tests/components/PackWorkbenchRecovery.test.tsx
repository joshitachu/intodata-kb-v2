import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getCurrent: vi.fn(),
  listPacks: vi.fn(),
  listPackFiles: vi.fn(),
  readPackFile: vi.fn(),
  createSession: vi.fn(),
}));

vi.mock("@/lib/packs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/packs")>();
  return {
    ...actual,
    getCurrentPackArchitectSession: mocks.getCurrent,
    listPacks: mocks.listPacks,
    listPackFiles: mocks.listPackFiles,
    readPackFile: mocks.readPackFile,
    createPackArchitectSession: mocks.createSession,
    starterInfo: vi.fn().mockResolvedValue({
      repo_url: "https://example.test/packs",
      default_branch: "main",
    }),
  };
});

import { PackWorkbench } from "@/components/packs/PackWorkbench";

const unfinishedDraft = {
  id: "0123456789abcdef0123456789abcdef",
  status: "active",
  revision: 1,
  source_pack_id: null,
  platform: null,
  model_id: "kimi/kimi-k2.5",
  messages: [],
  manifest_yaml: "name: swap-pack-starter\nversion: 0.1.0\n",
  files: { "README.md": "# Starter" },
  validation_errors: ["draft needs work"],
  complete: false,
  name: "swap-pack-starter",
  description: "Starter",
  turn_status: "idle",
  turn_stage: null,
  turn_detail: null,
  turn_started_at: null,
  turn_finished_at: null,
  turn_error: null,
};

const personalPack = {
  id: 41,
  name: "lineage-review",
  manifest_name: "lineage-review",
  version: "1.0.0",
  description: "Trace lineage",
  repo_url: null,
  repo_branch: null,
  last_commit_sha: null,
  auto_update: false,
  origin: "personal",
  certification_status: "personal",
  certification_provenance: null,
  personal: true,
  usable: true,
  mutable: true,
  blocked_reason: null,
};

describe("Pack Workbench draft recovery", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mocks.getCurrent.mockReset();
    mocks.getCurrent.mockResolvedValue(unfinishedDraft);
    mocks.listPacks.mockReset();
    mocks.listPacks.mockResolvedValue([]);
    mocks.listPackFiles.mockReset();
    mocks.listPackFiles.mockResolvedValue(["plugin.yaml", "README.md"]);
    mocks.readPackFile.mockReset();
    mocks.readPackFile.mockResolvedValue({ path: "plugin.yaml", content: "name: lineage-review" });
    mocks.createSession.mockReset();
  });

  it("keeps New pack clean and makes recovery explicit", async () => {
    render(<PackWorkbench />);

    fireEvent.click(await screen.findByRole("button", { name: "New pack" }));

    expect(screen.getByRole("heading", { name: "New pack" })).toBeInTheDocument();
    expect(screen.getByLabelText("What should this Pack do?")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Pack Architect" })).not.toBeInTheDocument();

    const resume = await screen.findByRole("button", { name: "Resume previous draft" });
    expect(screen.getByText(/swap pack starter/i)).toBeInTheDocument();

    fireEvent.click(resume);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Pack Architect" })).toBeInTheDocument();
    });
    expect(window.localStorage.getItem("nexus-pack-architect-session")).toBe(
      unfinishedDraft.id,
    );
  });

  it("starts AI refinement from an existing personal Pack", async () => {
    mocks.listPacks.mockResolvedValue([personalPack]);
    mocks.createSession.mockResolvedValue({
      ...unfinishedDraft,
      id: "abcdefabcdefabcdefabcdefabcdefab",
      source_pack_id: personalPack.id,
      name: personalPack.name,
    });
    render(<PackWorkbench />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(await screen.findByRole("button", { name: "Refine with AI" }));

    expect(
      await screen.findByRole("heading", { name: `Refine ${personalPack.name}` }),
    ).toBeInTheDocument();
    expect(mocks.createSession).toHaveBeenCalledWith(undefined, personalPack.id);
  });
});
