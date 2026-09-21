import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DocumentUpload } from "@/components/DocumentUpload";

const mutate = vi.fn();
const mutateAsync = vi.fn();
const setSelectedProject = vi.fn();

// Mutable so a single test can run as an ordinary project member. `vi.mock` is
// hoisted above the module body, so the flag has to be hoisted with it.
const permissions = vi.hoisted(() => ({ canManageProjects: true }));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    hasPermission: (codename: string) =>
      codename === "manage_projects" ? permissions.canManageProjects : false,
    isOwner: false,
    hasAnyAdminPermission: permissions.canManageProjects,
    canManageUsers: false,
    canManageRoles: false,
    canAssignProjects: false,
    canCreateProjects: false,
    canIngestDocuments: true,
    canViewAuditLogs: false,
  }),
}));

vi.mock("@/hooks/useDocuments", () => ({
  useUploadDocument: () => ({
    mutate,
    mutateAsync,
    isPending: false,
    isSuccess: false,
    isError: false,
  }),
}));

vi.mock("@/hooks/useDocsProjects", () => ({
  useDocsProjects: () => ({
    data: {
      projects: [
        { id: "proj", display_name: "Project", platform: "talend" },
      ],
    },
    isLoading: false,
  }),
}));

vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => ({
    selectedProject: "proj",
    setSelectedProject,
  }),
}));

/**
 * The routing table the form reads to say where a file will land. Stubbed at
 * `fetch` rather than at the hook so the URL the form calls stays part of what
 * these tests pin.
 */
const ROUTING = {
  routes: [
    {
      role: "source",
      kind: "evidence",
      destination: null,
      always_loaded: false,
      materializes: false,
      description: "Evidence the assistant cites.",
      materialize_max_chars: null,
    },
    {
      role: "guidance",
      kind: "method",
      destination: ".nexus/project/skills/<id>-<slug>/SKILL.md",
      always_loaded: false,
      materializes: true,
      description: "A method the assistant follows when doing that kind of work.",
      materialize_max_chars: null,
    },
    {
      role: "policy",
      kind: "constraint",
      destination: ".nexus/project/references/<id>-<slug>.md",
      always_loaded: false,
      materializes: true,
      description: "A requirement every answer must respect.",
      materialize_max_chars: null,
    },
  ],
  materialize_max_chars: 32000,
};

function stubRoutingFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/guidance/document-routing")) {
        return {
          ok: true,
          status: 200,
          json: async () => ROUTING,
          text: async () => JSON.stringify(ROUTING),
        } as Response;
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
}

function renderUpload() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DocumentUpload />
    </QueryClientProvider>,
  );
}

describe("DocumentUpload", () => {
  beforeEach(() => {
    mutate.mockClear();
    mutateAsync.mockReset();
    mutateAsync.mockResolvedValue({ message: "Ingested 1 chunks" });
    setSelectedProject.mockClear();
    permissions.canManageProjects = true;
    stubRoutingFetch();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("queues the selected document role with the uploaded file", async () => {
    const { container } = renderUpload();
    const roleSelect = screen.getByLabelText("Role");
    fireEvent.change(roleSelect, { target: { value: "policy" } });

    const fileInput = container.querySelector('input[type="file"]');
    const file = new File(["content"], "policy.pdf", { type: "application/pdf" });

    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput!, { target: { files: [file] } });

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      file,
      project_id: "proj",
      document_role: "policy",
    }));
  });

  // A non-source role becomes guidance the assistant follows for everyone on the
  // project, so it is gated on `manage_projects`. The backend is the authority
  // (403); these two tests cover the client half, whose only job is to stop a
  // user spending a 50 MB upload on a request that will be refused.
  it("locks privileged roles for a member without manage_projects", () => {
    permissions.canManageProjects = false;
    renderUpload();

    const source = screen.getByRole("option", { name: /^Source/ }) as HTMLOptionElement;
    expect(source.disabled).toBe(false);

    for (const label of ["Reference", "Guidance", "Standard", "Template", "Policy"]) {
      const option = screen.getByRole("option", {
        name: new RegExp(`^${label}`),
      }) as HTMLOptionElement;
      expect(option.disabled).toBe(true);
      expect(option.textContent).toContain("project managers only");
    }
  });

  it("sends source even if an unprivileged session holds a privileged role", async () => {
    permissions.canManageProjects = false;
    const { container } = renderUpload();

    // Drive the select straight to a disabled value, which is what a stale
    // session or a tampered client would produce. The disabled attribute is
    // presentation; the clamp is what decides what actually gets sent.
    const roleSelect = screen.getByLabelText("Role");
    fireEvent.change(roleSelect, { target: { value: "policy" } });

    const fileInput = container.querySelector('input[type="file"]');
    const file = new File(["content"], "policy.pdf", { type: "application/pdf" });
    fireEvent.change(fileInput!, { target: { files: [file] } });

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      file,
      project_id: "proj",
      document_role: "source",
    }));
  });

  it("queues every selected file and processes them one at a time", async () => {
    const { container } = renderUpload();
    const fileInput = container.querySelector('input[type="file"]');
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["# Second"], "second.md", { type: "text/markdown" });

    expect(fileInput).toHaveProperty("multiple", true);
    fireEvent.change(fileInput!, { target: { files: [first, second] } });

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
    expect(mutateAsync).toHaveBeenNthCalledWith(1, {
      file: first,
      project_id: "proj",
      document_role: "source",
    });
    expect(mutateAsync).toHaveBeenNthCalledWith(2, {
      file: second,
      project_id: "proj",
      document_role: "source",
    });
    expect(await screen.findAllByText(/Complete —/)).toHaveLength(2);
  });

  // The role select decides what a document *becomes*, and a user cannot make
  // that choice deliberately without being told the consequence before the
  // upload rather than after it.
  it("shows where the selected role lands in the workspace", async () => {
    renderUpload();

    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "guidance" } });

    const hint = await screen.findByTestId("upload-routing-hint");
    expect(hint).toHaveTextContent(".nexus/project/skills/<id>-<slug>/SKILL.md");
    expect(hint).toHaveTextContent("read on demand");
    expect(hint).toHaveTextContent(
      "A method the assistant follows when doing that kind of work.",
    );
  });

  it("says a source is searched rather than mounted, and names the ceiling", async () => {
    renderUpload();

    const hint = await screen.findByTestId("upload-routing-hint");
    expect(hint).toHaveTextContent(/Searched, never mounted/i);
    expect(hint).not.toHaveTextContent(".nexus/");
    // The one outcome users find surprising: the right role is not enough.
    expect(hint).toHaveTextContent(/Over 32,000 characters/i);
  });

  it("shows the clamped destination, not the one the select claims", async () => {
    // A member without manage_projects has their role clamped to source at
    // enqueue. Promising them a skill file would be a lie the upload disproves.
    permissions.canManageProjects = false;
    renderUpload();

    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "guidance" } });

    const hint = await screen.findByTestId("upload-routing-hint");
    expect(hint).toHaveTextContent(/Searched, never mounted/i);
    expect(hint).not.toHaveTextContent("SKILL.md");
  });

  it("keeps the form usable when the routing table cannot be read", async () => {
    // A refusal rather than a dropped connection: a transport failure is
    // retried by `retryTransientOnly` and is not yet a final answer, so it is
    // not the state this test is about.
    const refusal = { error: "Forbidden", code: "forbidden", retryable: false };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 403,
        json: async () => refusal,
        text: async () => JSON.stringify(refusal),
      }) as Response),
    );
    const { container } = renderUpload();

    expect(await screen.findByText(/Could not read the routing table/i)).toBeInTheDocument();

    // The upload itself is unaffected by a failed hint.
    const fileInput = container.querySelector('input[type="file"]');
    const file = new File(["content"], "notes.md", { type: "text/markdown" });
    fireEvent.change(fileInput!, { target: { files: [file] } });

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      file,
      project_id: "proj",
      document_role: "source",
    }));
  });
});
