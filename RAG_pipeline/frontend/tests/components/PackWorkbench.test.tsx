import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PackWorkbench } from "@/components/harness-packs/PackWorkbench";
import { stagePackCreateCommand } from "@/lib/packWorkspace";

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  send: vi.fn(),
  create: vi.fn(),
  close: vi.fn(),
  activeId: 41 as number | null,
  streamState: { isStreaming: false, workspace: null as "pack" | null },
  assistResponse: null as Promise<Response> | null,
  uploadResponse: null as Promise<Response> | null,
}));

vi.mock("@/contexts/ChatContext", () => ({
  useChatContextSafe: () => ({ activeConversationId: mocks.activeId, streamState: mocks.streamState, handleSendMessage: mocks.send, createConversation: mocks.create }),
}));

const artifact = {
  purpose: "Support a supply allocation decision.",
  analyses: [{ id: "exposure", label: "Exposure", purpose: "Map supplier exposure." }],
  evidence_requirements: [{ id: "supplier-data", label: "Supplier data", required: true, weight: 70 }],
  output_documents: [{ id: "brief", title: "Decision brief", sections: [{ id: "summary", heading: "Summary", order: 1 }] }],
  audience: { label: "Regional procurement directors", decision_needs: ["Allocate supply"] },
};
const attachment = { id: 4, filename: "supplier-risk.pdf", content_type: "application/pdf", size_bytes: 2048, sha256: "a".repeat(64), purpose: "Assess supplier risk", extracted_text_preview: "Supplier concentration is high.", created_at: "2026-07-17T10:00:00Z" };
const draft = { id: 7, conversation_id: 41 as number | null, revision: 1, status: "active", name: "Supply Analysis", artifact, attachments: [attachment], validation: { valid: true, errors: [] } };
const version = { id: 12, name: "Supply Analysis", version: "1.0.0", artifact, attachments: [attachment] };

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("PackWorkbench", () => {
  let currentDraft: typeof draft;
  let nextAttachmentId: number;

  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    currentDraft = structuredClone(draft);
    mocks.activeId = 41;
    mocks.streamState = { isStreaming: false, workspace: null };
    mocks.assistResponse = null;
    mocks.uploadResponse = null;
    nextAttachmentId = 5;
    mocks.send.mockResolvedValue(undefined);
    mocks.create.mockResolvedValue(42);
    mocks.fetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/private") && method === "GET") return response([version]);
      if (url.endsWith("/drafts") && method === "POST") return response(currentDraft, 201);
      if (url.endsWith("/private/12/drafts") && method === "POST") return response(currentDraft, 201);
      if (url.endsWith("/drafts/7/assist") && method === "POST") {
        currentDraft = { ...currentDraft, conversation_id: null, revision: currentDraft.revision + 1, artifact: { ...currentDraft.artifact, purpose: "Updated by the assistant." } };
        return mocks.assistResponse ?? response({ draft: currentDraft, message: "I updated the Pack inside the workbench." });
      }
      if (url.endsWith("/drafts/7") && method === "PATCH") return response(currentDraft);
      if (url.endsWith("/drafts/7/attachments") && method === "POST") {
        const form = init?.body as FormData;
        const file = form.get("file") as File;
        return mocks.uploadResponse ?? response({ ...attachment, id: nextAttachmentId++, filename: file.name, size_bytes: file.size, purpose: String(form.get("purpose")) }, 201);
      }
      if (url.endsWith("/drafts/7/attachments/4") && method === "PATCH") return response({ ...attachment, purpose: "Updated purpose" });
      if (url.endsWith("/drafts/7/attachments/4") && method === "DELETE") return response({});
      return response({ error: `Unhandled ${method} ${url}` }, 500);
    });
    vi.stubGlobal("fetch", mocks.fetch);
  });

  it("shows the section inventory, marks edits, and provides preview and changes modes", async () => {
    render(<PackWorkbench onClose={mocks.close} />);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    expect(await screen.findByDisplayValue("Supply Analysis")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Purpose"), { target: { value: "Protect the supply decision." } });
    expect(screen.getByLabelText("Changed")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("Preview"));
    expect(screen.getByText("Protect the supply decision.")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("Changes"));
    expect(screen.getByText("This section has changes")).toBeInTheDocument();
  });

  it("requires an attachment purpose and sends multipart upload, then supports reference editing and deletion", async () => {
    render(<PackWorkbench onClose={mocks.close} />);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    await screen.findByDisplayValue("Supply Analysis");
    fireEvent.click(screen.getByRole("button", { name: /References/ }));
    const uploadButton = screen.getByRole("button", { name: "Upload reference" });
    expect(uploadButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reference purpose"), { target: { value: "Provide decision context" } });
    const file = new File(["brief"], "brief.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("Attachment file"), { target: { files: [file] } });
    fireEvent.click(uploadButton);
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/drafts/7/attachments"), expect.objectContaining({ method: "POST", body: expect.any(FormData) })));
    fireEvent.change(screen.getByLabelText("Purpose for supplier-risk.pdf"), { target: { value: "Updated purpose" } });
    fireEvent.blur(screen.getByLabelText("Purpose for supplier-risk.pdf"));
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/attachments/4"), expect.objectContaining({ method: "PATCH" })));
    fireEvent.click(screen.getByRole("button", { name: "Delete supplier-risk.pdf" }));
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/attachments/4"), expect.objectContaining({ method: "DELETE" })));
  });

  it("forks a saved version into a detached draft and edits it in place", async () => {
    render(<PackWorkbench initialPackId="12" onClose={mocks.close} />);
    expect(await screen.findByRole("button", { name: "Edit saved version" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit saved version" }));
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/private/12/drafts"), expect.objectContaining({ method: "POST" })));
    fireEvent.change(screen.getByLabelText("Ask the Pack assistant"), { target: { value: "Make it more concise" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/drafts/7/assist"), expect.objectContaining({ method: "POST", body: JSON.stringify({ instruction: "Make it more concise" }) })));
    expect(mocks.send).not.toHaveBeenCalled();
    expect(mocks.create).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue("Updated by the assistant.")).toBeInTheDocument();
  });

  it("shows nearby indeterminate progress while the Pack assistant is working", async () => {
    let resolveAssist!: (value: Response) => void;
    mocks.assistResponse = new Promise((resolve) => { resolveAssist = resolve; });
    render(<PackWorkbench onClose={mocks.close} />);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    await screen.findByDisplayValue("Supply Analysis");
    fireEvent.change(screen.getByLabelText("Ask the Pack assistant"), { target: { value: "Use three documents" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(screen.getByRole("progressbar", { name: "Reviewing your request and updating the Pack" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Updating" })).toBeDisabled();

    await act(async () => {
      resolveAssist(await response({ draft: currentDraft, message: "Updated." }));
    });
    await waitFor(() => expect(screen.queryByRole("progressbar")).not.toBeInTheDocument());
  });

  it("uploads multiple described context files before asking the Pack assistant", async () => {
    render(<PackWorkbench onClose={mocks.close} />);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    await screen.findByDisplayValue("Supply Analysis");

    const template = new File(["# Template"], "manager-template.md", { type: "text/markdown" });
    const scope = new File(["Scope: payment integrations"], "scope.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Context files"), { target: { files: [template, scope] } });
    fireEvent.change(screen.getByLabelText("Purpose for queued manager-template.md"), { target: { value: "Use as the output template" } });
    fireEvent.change(screen.getByLabelText("Purpose for queued scope.txt"), { target: { value: "Defines the analysis scope" } });
    fireEvent.change(screen.getByLabelText("Ask the Pack assistant"), { target: { value: "Build the Pack from these files" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/drafts/7/assist"), expect.anything()));
    const relevantCalls = mocks.fetch.mock.calls.filter(([input]) => String(input).includes("/drafts/7/attachments") || String(input).includes("/drafts/7/assist"));
    expect(relevantCalls.map(([input]) => String(input).split("/").at(-1))).toEqual(["attachments", "attachments", "assist"]);
    const firstForm = relevantCalls[0][1]?.body as FormData;
    const secondForm = relevantCalls[1][1]?.body as FormData;
    expect((firstForm.get("file") as File).name).toBe("manager-template.md");
    expect(firstForm.get("purpose")).toBe("Use as the output template");
    expect((secondForm.get("file") as File).name).toBe("scope.txt");
    expect(secondForm.get("purpose")).toBe("Defines the analysis scope");
    expect(mocks.send).not.toHaveBeenCalled();
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("shows which context file is being attached before agent reasoning starts", async () => {
    let resolveUpload!: (value: Response) => void;
    mocks.uploadResponse = new Promise((resolve) => { resolveUpload = resolve; });
    render(<PackWorkbench onClose={mocks.close} />);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    await screen.findByDisplayValue("Supply Analysis");
    const file = new File(["scope"], "scope.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Context files"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("Purpose for queued scope.txt"), { target: { value: "Defines scope" } });
    fireEvent.change(screen.getByLabelText("Ask the Pack assistant"), { target: { value: "Use this scope" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(screen.getByRole("progressbar", { name: "Attaching 1 of 1: scope.txt" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Attaching" })).toBeDisabled();

    await act(async () => {
      resolveUpload(await response({ ...attachment, id: 5, filename: "scope.txt", purpose: "Defines scope" }, 201));
    });
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/drafts/7/assist"), expect.anything()));
  });

  it("requires a purpose for every queued context file", async () => {
    render(<PackWorkbench onClose={mocks.close} />);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    await screen.findByDisplayValue("Supply Analysis");
    const file = new File(["requirements"], "requirements.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Context files"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("Ask the Pack assistant"), { target: { value: "Use this" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Explain how Nexus should use every attached file before sending.");
    expect(mocks.fetch).not.toHaveBeenCalledWith(expect.stringContaining("/drafts/7/assist"), expect.anything());
  });

  it("can create a Pack request from described files without extra prompt text", async () => {
    render(<PackWorkbench onClose={mocks.close} />);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    await screen.findByDisplayValue("Supply Analysis");
    const file = new File(["Create three security reports"], "pack-definition.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("Context files"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("Purpose for queued pack-definition.md"), { target: { value: "This file defines the complete Pack" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/drafts/7/assist"),
      expect.objectContaining({ body: JSON.stringify({ instruction: "Create or refine this Pack using the attached context files and their stated purposes." }) }),
    ));
  });

  it("renders saved versions as readable sections and only edits by forking", async () => {
    render(<PackWorkbench initialPackId="12" onClose={mocks.close} />);
    expect(await screen.findByText("Support a supply allocation decision.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Analysis focus" }));
    expect(screen.getByText("Map supplier exposure.")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Map supplier exposure.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Output documents" }));
    expect(screen.getByText("Decision brief")).toBeInTheDocument();
    expect(screen.getByText("Summary")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit saved version" }));
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/private/12/drafts"), expect.objectContaining({ method: "POST" })));
  });

  it("creates a detached draft and handles a staged create command inside the workbench", async () => {
    stagePackCreateCommand("/pack create a concise decision brief");
    render(<PackWorkbench initialPackId="create" onClose={mocks.close} />);
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/drafts"), expect.objectContaining({ method: "POST" })));
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/drafts/7/assist"), expect.objectContaining({ method: "POST", body: JSON.stringify({ instruction: "/pack create a concise decision brief" }) })));
    expect(mocks.send).not.toHaveBeenCalled();
    expect(mocks.create).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue("Updated by the assistant.")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("pack-workbench-create-command")).toBeNull();
  });

  it("creates a Pack in place when the workbench starts without a conversation", async () => {
    mocks.activeId = null;
    mocks.create.mockResolvedValue(99);
    render(<PackWorkbench onClose={mocks.close} />);

    fireEvent.click(screen.getByRole("button", { name: "New" }));

    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/drafts"),
      expect.objectContaining({ method: "POST", body: expect.not.stringContaining("conversation_id") }),
    ));
    expect(mocks.create).not.toHaveBeenCalled();
    expect(mocks.send).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue("Supply Analysis")).toBeInTheDocument();
  });

  it("creates an editable draft when refining a saved Pack in natural language", async () => {
    render(<PackWorkbench initialPackId="12" onClose={mocks.close} />);
    await screen.findByText("Support a supply allocation decision.");
    fireEvent.change(screen.getByLabelText("Refine saved Pack"), { target: { value: "Add a one-page board memo" } });
    fireEvent.click(screen.getByRole("button", { name: "Refine" }));

    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith(expect.stringContaining("/drafts/7/assist"), expect.objectContaining({ method: "POST", body: JSON.stringify({ instruction: "Add a one-page board memo" }) })));
    expect(mocks.create).not.toHaveBeenCalled();
    expect(mocks.send).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue("Updated by the assistant.")).toBeInTheDocument();
  });
});
