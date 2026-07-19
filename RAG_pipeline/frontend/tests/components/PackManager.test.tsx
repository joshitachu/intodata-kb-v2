import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PackManager } from "@/components/harness-packs/PackManager";

const mocks = vi.hoisted(() => ({
  useUserProjects: vi.fn(),
  fetchGovernancePolicy: vi.fn(),
  fetchCertifiedPackLibrary: vi.fn(),
  fetchPrivateStylePacks: vi.fn(),
  fetchProjectStylePacks: vi.fn(),
  fetchGovernedPackActivation: vi.fn(),
  fetchDocumentationPacks: vi.fn(),
  fetchDocumentationPackSelection: vi.fn(),
}));

vi.mock("@/hooks/useUserProjects", () => ({ useUserProjects: mocks.useUserProjects }));
vi.mock("@/contexts/ChatContext", () => ({ useChatContextSafe: () => null }));
vi.mock("@/lib/harnessPacks", () => ({
  createPrivateStylePack: vi.fn(),
  createProjectStylePack: vi.fn(),
  fetchGovernancePolicy: mocks.fetchGovernancePolicy,
  fetchCertifiedPackLibrary: mocks.fetchCertifiedPackLibrary,
  fetchPrivateStylePacks: mocks.fetchPrivateStylePacks,
  fetchProjectStylePacks: mocks.fetchProjectStylePacks,
  fetchGovernedPackActivation: mocks.fetchGovernedPackActivation,
  fetchDocumentationPacks: mocks.fetchDocumentationPacks,
  fetchDocumentationPackSelection: mocks.fetchDocumentationPackSelection,
  saveDocumentationPackSelection: vi.fn(),
  saveGovernedPackActivation: vi.fn(),
  savePrivateStylePackVersion: vi.fn(),
  saveProjectStylePackVersion: vi.fn(),
}));

const corePack = {
  id: "nexus.documentation", name: "Core Governance", version: "1.0.0", content_hash: "c".repeat(64),
  pack_type: "documentation" as const, phase: "foundation" as const, authority: "intodata_certified", trust: "certified",
  mutability: "immutable", scope: "global", owner: "intodata", quality_score: 0.95, evaluation_score: 0.94,
  includes: [], workflow_labels: ["Document"], workflow_descriptions: ["Adds governed documentation workflows."],
  entity_labels: ["Document"], domains: ["documentation"], documentation_provider_ref: null,
  blocked_override_rules: ["organization policy is mandatory"],
};
const functionalPack = {
  ...corePack,
  id: "nexus.operations",
  name: "Operations Analysis",
  phase: "functional" as const,
  content_hash: "d".repeat(64),
};
const policy = {
  id: "org.default", required_certified_pack_ids: [corePack.id], allowed_functional_pack_ids: [],
  permitted_style: { permitted_fields: [], allowed_audiences: [], allowed_tones: [], allowed_technical_depths: [], allowed_output_organizations: [], allowed_formatting: [], allowed_communication: [] },
  minimum_quality: { quality_score: 0.8, evaluation_score: 0.8 }, protected_invariants: [],
};

describe("PackManager optional documentation integration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchGovernancePolicy.mockResolvedValue(policy);
    mocks.fetchCertifiedPackLibrary.mockResolvedValue([corePack]);
    mocks.fetchPrivateStylePacks.mockResolvedValue([]);
    mocks.fetchProjectStylePacks.mockResolvedValue([]);
    mocks.fetchGovernedPackActivation.mockResolvedValue(null);
  });

  it("keeps the governed project stack usable when MCP selection loading fails", async () => {
    mocks.useUserProjects.mockReturnValue({ data: { projects: [{ id: "project-a", display_name: "Project A" }] }, isLoading: false });
    mocks.fetchDocumentationPackSelection.mockRejectedValue(new Error("MCP selection offline"));
    render(<PackManager initialProjectId="project-a" adminMode />);

    expect(screen.getByText("Documentation Packs")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Organization and advanced settings"));
    expect((await screen.findAllByText("Core Governance")).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Save stack" })).toBeInTheDocument();
    expect(mocks.fetchGovernedPackActivation).toHaveBeenCalledWith("project-a", "documentation.generate");
    expect(await screen.findByText(/Organization Pack selection is currently unavailable/)).toHaveTextContent("MCP selection offline");
  });

  it("keeps governed library browsing usable when the MCP catalog fails", async () => {
    mocks.useUserProjects.mockReturnValue({ data: { projects: [] }, isLoading: false });
    mocks.fetchDocumentationPacks.mockRejectedValue(new Error("MCP catalog offline"));
    render(<PackManager adminMode />);

    fireEvent.click(screen.getByText("Organization and advanced settings"));
    expect(await screen.findByText("Core Governance")).toBeInTheDocument();
    expect(screen.getByText("Style / Presentation")).toBeInTheDocument();
    expect(await screen.findByText(/Organization Pack selection is currently unavailable/)).toHaveTextContent("MCP catalog offline");
  });

  it("preselects a requested accessible Functional Pack without activating it", async () => {
    mocks.useUserProjects.mockReturnValue({ data: { projects: [{ id: "project-a", display_name: "Project A" }] }, isLoading: false });
    mocks.fetchCertifiedPackLibrary.mockResolvedValue([corePack, functionalPack]);
    mocks.fetchDocumentationPackSelection.mockResolvedValue({ project_id: "project-a", selected_plugin_pack_id: null, packs: [] });
    render(<PackManager initialProjectId="project-a" initialPackName="Operations Analysis" adminMode />);

    expect(await screen.findByRole("checkbox", { name: /Operations Analysis/ })).toBeChecked();
  });

  it("keeps a requested documentation Pack pending until explicit save", async () => {
    mocks.useUserProjects.mockReturnValue({ data: { projects: [{ id: "project-a", display_name: "Project A" }] }, isLoading: false });
    mocks.fetchDocumentationPackSelection.mockResolvedValue({
      project_id: "project-a",
      selected_plugin_pack_id: null,
      packs: [{ id: 7, name: "Talend Migration", version: "2.0.0", description: null }],
    });
    render(<PackManager initialProjectId="project-a" initialPackName="Talend Migration" />);

    expect(await screen.findByText("Pending documentation Pack selection. Save to apply it to this project.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use for this project" })).toBeEnabled();
    expect(screen.queryByText("Organization and advanced settings")).not.toBeInTheDocument();
  });
});
