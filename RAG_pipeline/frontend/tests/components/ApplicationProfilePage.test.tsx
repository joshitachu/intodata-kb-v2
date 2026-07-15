import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ApplicationProfilePage from "@/app/admin/application-profile/page";

const mocks = vi.hoisted(() => ({
  useUserProjects: vi.fn(),
  fetchPackCatalog: vi.fn(),
  fetchPackRecommendations: vi.fn(),
  savePackActivation: vi.fn(),
  createCustomPack: vi.fn(),
}));

vi.mock("@/hooks/useUserProjects", () => ({ useUserProjects: mocks.useUserProjects }));
vi.mock("@/lib/harnessPacks", () => ({
  fetchPackCatalog: mocks.fetchPackCatalog,
  fetchPackRecommendations: mocks.fetchPackRecommendations,
  savePackActivation: mocks.savePackActivation,
  createCustomPack: mocks.createCustomPack,
}));

const policyPack = {
  id: "operations.default",
  name: "Operations",
  version: "1.0.0",
  pack_type: "domain" as const,
  includes: [],
  workflow_labels: ["Investigate"],
  workflow_descriptions: ["Operational investigation policy."],
  entity_labels: ["Execution"],
  documentation_provider_ref: null,
  content_hash: "a".repeat(64),
};

describe("ApplicationProfilePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useUserProjects.mockReturnValue({
      data: { projects: [{ id: "project-a", display_name: "Project A" }] },
      isLoading: false,
    });
    mocks.fetchPackCatalog.mockResolvedValue({
      packs: [policyPack],
      active: { active_pack_ids: [], resolved_packs: [], snapshot_hash: "b".repeat(64) },
      total: 1,
    });
    mocks.fetchPackRecommendations.mockResolvedValue([]);
    mocks.savePackActivation.mockResolvedValue({
      project_id: "project-a",
      workflow_key: "default",
      active_pack_ids: [policyPack.id],
      snapshot: {
        active_pack_ids: [policyPack.id],
        resolved_packs: [{ id: policyPack.id, version: policyPack.version, content_hash: policyPack.content_hash }],
        snapshot_hash: "c".repeat(64),
      },
    });
  });

  it("saves capability policy through the admin Application Profile", async () => {
    render(<ApplicationProfilePage />);

    await screen.findByText("Operations");
    fireEvent.click(screen.getByRole("button", { name: "Select" }));
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));

    await waitFor(() => {
      expect(mocks.savePackActivation).toHaveBeenCalledWith(
        "project-a",
        "default",
        ["operations.default"],
      );
    });
  });
});
