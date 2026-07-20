import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AnalysisPackBuilder } from "@/components/harness-packs/AnalysisPackBuilder";

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  handleSendMessage: vi.fn(),
  createConversation: vi.fn(),
}));

vi.mock("@/contexts/ChatContext", () => ({
  useChatContextSafe: () => ({
    activeConversationId: 41,
    handleSendMessage: mocks.handleSendMessage,
    createConversation: mocks.createConversation,
  }),
}));

const artifact = {
  schema_version: 3,
  purpose: "Support a supply allocation decision.",
  analyses: [{ id: "exposure", label: "Exposure", purpose: "Map exposure." }],
  evidence_requirements: [{ id: "supplier-data", label: "Supplier data", required: true, weight: 70 }],
  output_documents: [{ id: "brief", title: "Decision brief", sections: [{ id: "summary", heading: "Summary", order: 1 }] }],
  scoring_dimensions: [{ id: "impact", label: "Impact", weight: 100 }],
  capability_requirements: [{ capability_id: "rag.docs.search_evidence", required: true, purpose: "Find supplier evidence." }],
  compatibility: { supported_project_kinds: ["supply-chain"], required_domains: ["procurement"] },
  audience: {
    label: "Regional procurement directors",
    roles: ["procurement director"],
    decision_needs: ["allocate supply"],
    domain_familiarity: "Regulated supply chain",
    desired_outcome: "Protect patient supply",
    render: { seniority: "director", domain_familiarity: "expert", decision_horizon: "immediate", detail_level: "brief" },
  },
};

const draft = {
  id: 7,
  conversation_id: 41,
  owner_user_id: 9,
  status: "active",
  revision: 1,
  name: "Supply Analysis",
  artifact,
  validation: { valid: true, errors: [], unknown_capabilities: [] },
};

const version = {
  id: 12,
  pack_id: "analysis.supply-7",
  owner_user_id: 9,
  name: "Supply Analysis",
  version: "1.0.0",
  content_hash: "a".repeat(64),
  artifact,
  receipt: {},
};

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("AnalysisPackBuilder", () => {
  let currentDraft = structuredClone(draft);
  let savedVersions = [version];

  beforeEach(() => {
    vi.clearAllMocks();
    currentDraft = structuredClone(draft);
    savedVersions = [version];
    mocks.handleSendMessage.mockResolvedValue(undefined);
    mocks.fetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/private") && method === "GET") return response(savedVersions);
      if (url.endsWith("/drafts") && method === "POST") return response(currentDraft, 201);
      if (url.endsWith("/drafts/7") && method === "GET") return response(currentDraft);
      if (url.endsWith("/drafts/7") && method === "PATCH") {
        const payload = JSON.parse(String(init?.body));
        currentDraft = { ...currentDraft, revision: currentDraft.revision + 1, artifact: { ...currentDraft.artifact, ...payload.patch } };
        return response(currentDraft);
      }
      if (url.endsWith("/drafts/7/validate")) return response(currentDraft);
      if (url.endsWith("/drafts/7/save")) return response(version, 201);
      if (url.endsWith("/private/12/compatibility")) return response({ project_id: "project-a", compatible: true, missing_required: [], missing_optional: [] });
      if (url.endsWith("/private/12/preview")) return response({ markdown: "# Decision brief\n\n## Summary\nEvidence-backed findings pending." });
      if (url.endsWith("/private/12/share")) return response({ project_id: "project-a", shared: true });
      if (url.endsWith("/private/12/activate")) return response({ project_id: "project-a", activation_snapshot_hash: "snapshot-1" });
      if (url.endsWith("/private/12/promotion-proposals")) return response({ status: "blocked_not_configured", detail: { reason: "Review setup is not available." } }, 201);
      return response({ error: `Unhandled request: ${method} ${url}` }, 500);
    });
    vi.stubGlobal("fetch", mocks.fetch);
  });

  it("progressively discloses structured and advanced artifact controls", async () => {
    render(<AnalysisPackBuilder />);
    fireEvent.click(screen.getByRole("button", { name: "New Pack" }));

    expect(await screen.findByRole("heading", { name: "Your Pack at a glance" })).toBeInTheDocument();
    expect(screen.getByText("Review and refine this Pack").closest("details")).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("Review and refine this Pack"));
    expect(screen.getByRole("heading", { name: "Purpose and audience" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Analyses" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Output documents and sections" })).toBeInTheDocument();
    expect(screen.getByText("Advanced Pack settings").closest("details")).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("Advanced Pack settings"));
    expect(screen.getByRole("heading", { name: "Evidence requirements" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Scoring" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Optional capability requirements" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Compatibility" })).toBeInTheDocument();
    expect(screen.getByLabelText("Audience description")).toHaveValue("Regional procurement directors");
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("sends the complete structured artifact through the builder proxy", async () => {
    render(<AnalysisPackBuilder />);
    fireEvent.click(screen.getByRole("button", { name: "New Pack" }));
    fireEvent.click(await screen.findByText("Review and refine this Pack"));
    const purpose = screen.getByLabelText("Purpose");
    fireEvent.change(purpose, { target: { value: "Support an urgent supply decision." } });
    fireEvent.click(screen.getByRole("button", { name: "Update" }));

    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/harness/packs/builder/drafts/7"), expect.objectContaining({ method: "PATCH" })));
    const patchRequest = mocks.fetch.mock.calls.find(([, init]) => init?.method === "PATCH");
    const payload = JSON.parse(String(patchRequest?.[1]?.body));
    expect(payload.patch).toMatchObject({ purpose: "Support an urgent supply decision.", analyses: artifact.analyses, evidence_requirements: artifact.evidence_requirements, output_documents: artifact.output_documents, audience: artifact.audience });
  });

  it("keeps chat creation primary and saves a validated private version", async () => {
    render(<AnalysisPackBuilder />);
    fireEvent.click(screen.getByRole("button", { name: "New Pack" }));
    expect(await screen.findByRole("heading", { name: "Refine with the assistant" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Message the Pack assistant" }), { target: { value: "Add a mitigation analysis." } });
    fireEvent.click(screen.getByRole("button", { name: "Ask assistant" }));
    await waitFor(() => expect(mocks.handleSendMessage).toHaveBeenCalledWith({ query: "/pack create Add a mitigation analysis." }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save Pack" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Save Pack" }));
    await waitFor(() => expect(screen.getByText(/Saved private version 1\.0\.0/)).toBeInTheDocument());
    expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/harness/packs/builder/drafts/7/save"), expect.objectContaining({ method: "POST" }));
  });

  it("supports project checks and clearly reports blocked promotion", async () => {
    render(<AnalysisPackBuilder />);
    fireEvent.click(await screen.findByRole("button", { name: /View/ }));
    fireEvent.click(screen.getByText("Advanced project actions"));
    fireEvent.change(screen.getByLabelText("Project ID"), { target: { value: "project-a" } });
    fireEvent.click(screen.getByRole("button", { name: "Check compatibility" }));
    await waitFor(() => expect(screen.getByText("Project check complete")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Propose promotion" }));
    await waitFor(() => expect(screen.getByText(/Blocked: platform setup is still required/)).toBeInTheDocument());
  });

  it("uses a saved Pack with the selected project without showing raw project controls", async () => {
    render(<AnalysisPackBuilder selectedProjectId="project-a" />);

    fireEvent.click(await screen.findByRole("button", { name: "Use with project" }));
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/harness/packs/builder/private/12/activate"), expect.objectContaining({ method: "POST" })));
    const activateRequest = mocks.fetch.mock.calls.find(([url, init]) => String(url).endsWith("/private/12/activate") && init?.method === "POST");
    expect(JSON.parse(String(activateRequest?.[1]?.body))).toEqual({ project_id: "project-a", workflow_key: "documentation.generate" });
    expect(screen.getByText("Pack is ready to use with the selected project.")).toBeInTheDocument();
    expect(screen.getByText("Advanced project actions").closest("details")).not.toHaveAttribute("open");
  });
});
