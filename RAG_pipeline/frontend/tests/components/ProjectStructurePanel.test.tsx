import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProjectStructurePanel } from "@/components/guidance/ProjectStructurePanel";

/**
 * The panel is the only place a user can read what the assistant is told, so
 * these tests pin the things that make it worth having.
 *
 * The shape being defended is that there is exactly one editing surface: the
 * pane beside the tree. Open a file and you read it and change it there. The
 * four editor cards that used to sit under the tree -- fifteen or so boxes
 * restating the same four files -- are gone, and a test here fails if anything
 * grows back outside the pane.
 *
 * The rest are about capability and comprehension: every level is visible
 * whether or not the user may edit it, the server's refusals (403 by
 * permission, 409 when a file is full) arrive as something actionable rather
 * than as a generic failure, and nothing is marked as an error before the user
 * has done anything.
 *
 * The network is stubbed at `fetch` rather than at the hook layer on purpose --
 * the URL each control calls is part of the contract being tested, and a mocked
 * hook would let a wrong endpoint pass.
 */

const permissions = vi.hoisted(() => ({ canManageProjects: true, isOwner: true }));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    hasPermission: (codename: string) =>
      codename === "manage_projects" ? permissions.canManageProjects : false,
    isOwner: permissions.isOwner,
    hasAnyAdminPermission: permissions.isOwner,
    canManageUsers: false,
    canManageRoles: false,
    canAssignProjects: false,
    canCreateProjects: false,
    canIngestDocuments: true,
    canViewAuditLogs: false,
  }),
}));

const initialProject = vi.hoisted(() => ({ id: "proj" }));

/**
 * A real, stateful selection.
 *
 * The panel owns a project picker, and a mock that pinned the selection to a
 * constant could not tell the difference between "the tree appears when you
 * choose a project" and "the tree never appears at all". `useChatContext` is
 * called exactly once per render tree, so holding the state in a hook inside
 * the mock is safe and gives the component the same behaviour the real context
 * has.
 */
vi.mock("@/contexts/ChatContext", async () => {
  const { useState } = await import("react");
  return {
    useChatContext: () => {
      const [selectedProject, setSelectedProject] = useState(initialProject.id);
      return { selectedProject, setSelectedProject };
    },
  };
});

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

const ORG_RULE = {
  id: 1,
  layer: "org",
  kind: "rule",
  project_id: null,
  user_id: null,
  content: "Answer in the language the question was asked in.",
  payload: null,
  status: "active",
  source: "admin_ui",
  supersedes_id: null,
  created_by: 1,
  created_at: "2026-09-01T10:00:00Z",
};

const PROJECT_RULE = {
  ...ORG_RULE,
  id: 7,
  layer: "project",
  project_id: "proj",
  content: "Treat the 4PS interface as read-only.",
};

const PROPOSED_RULE = {
  ...PROJECT_RULE,
  id: 9,
  content: "Always name the environment a run belongs to.",
  status: "proposed",
  source: "learned:conversation:42",
};

const PREVIEW = {
  project_id: "proj",
  user_id: 1,
  rendered: "## Rules (organization)\n- Answer in the language the question was asked in.",
  cache_key: "abc123",
  conflicts: [],
  layer_tokens: { org: 128, project: 64, person: 0 },
  layer_budgets: { org: 400, project: 600, person: 150 },
};

const PREFERENCES = {
  project_id: "proj",
  user_id: 1,
  style: null,
  rules: [],
};

const PROJECTS = {
  projects: [
    { id: "proj", display_name: "Reverse engineer VW", platform: "talend", doc_count: 3 },
    { id: "other", display_name: "Order intake", platform: "workato", doc_count: 1 },
  ],
  total: 2,
};

const ORG_FILE = ".nexus/org/RULES.md";
const PROJECT_FILE = ".nexus/project/RULES.md";
const PERSON_FILE = ".nexus/user/PREFERENCES.md";
const MANIFEST_FILE = ".nexus/project/MANIFEST.md";
const SKILL_FILE = ".nexus/project/skills/52-test/SKILL.md";

const WORKSPACE = {
  project_id: "proj",
  files: [
    {
      path: "AGENTS.md",
      origin: "assembled",
      always_loaded: true,
      bytes: 5967,
      content: "# AGENTS\n\nThe assembled stack.",
    },
    {
      path: ORG_FILE,
      origin: "org_layer",
      always_loaded: true,
      bytes: 756,
      content: "- Answer in the language the question was asked in.",
    },
    {
      path: PROJECT_FILE,
      origin: "project_layer",
      always_loaded: true,
      bytes: 512,
      content: "- Treat the 4PS interface as read-only.",
    },
    {
      path: PERSON_FILE,
      origin: "person_layer",
      always_loaded: true,
      bytes: 120,
      content: "- audience Engineering",
    },
    {
      path: MANIFEST_FILE,
      origin: "manifest",
      always_loaded: true,
      bytes: 412,
      content: "- Test method (skills/52-test/SKILL.md)",
    },
    {
      path: SKILL_FILE,
      origin: "document:52",
      always_loaded: false,
      bytes: 254,
      content: "# Test method\n\nHow to run the regression pack.",
    },
  ],
  docs_mounted: true,
  always_loaded_tokens: 441,
};

interface RouteOverrides {
  orgRules?: unknown[];
  projectRules?: unknown[];
  proposedRules?: unknown[];
  preferences?: unknown;
  preview?: unknown;
  workspace?: unknown;
  documents?: unknown;
  createProjectRule?: () => Response;
}

let calls: { url: string; init?: RequestInit }[] = [];

function stubFetch(overrides: RouteOverrides = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });

    if (url.startsWith("/api/docs/projects")) {
      return jsonResponse(PROJECTS);
    }
    if (url === "/api/guidance/org?status=active" && !init?.method) {
      return jsonResponse({ rules: overrides.orgRules ?? [ORG_RULE] });
    }
    if (url === "/api/guidance/projects/proj?status=active" && !init?.method) {
      return jsonResponse({ rules: overrides.projectRules ?? [PROJECT_RULE] });
    }
    if (url === "/api/guidance/projects/proj?status=proposed" && !init?.method) {
      return jsonResponse({ rules: overrides.proposedRules ?? [] });
    }
    if (url === "/api/guidance/projects/proj/preview") {
      return jsonResponse(overrides.preview ?? PREVIEW);
    }
    if (url === "/api/guidance/projects/proj/workspace") {
      return jsonResponse(overrides.workspace ?? WORKSPACE);
    }
    if (url === "/api/guidance/projects/proj/preferences" && !init?.method) {
      return jsonResponse(overrides.preferences ?? PREFERENCES);
    }
    if (url === "/api/guidance/projects/proj/preferences" && init?.method === "PUT") {
      return jsonResponse(PREFERENCES);
    }
    if (url === "/api/guidance/projects/proj" && init?.method === "POST") {
      return (overrides.createProjectRule ?? (() => jsonResponse(PROJECT_RULE, 201)))();
    }
    if (url === "/api/guidance/org" && init?.method === "POST") {
      return jsonResponse(ORG_RULE, 201);
    }
    if (url.startsWith("/api/guidance/org/") && init?.method === "DELETE") {
      return jsonResponse({}, 204);
    }
    if (url.startsWith("/api/guidance/projects/proj/rules/") && init?.method === "POST") {
      return jsonResponse({ ...PROPOSED_RULE, status: "active" });
    }
    if (url.startsWith("/api/guidance/projects/proj/rules/") && init?.method === "DELETE") {
      return jsonResponse({}, 204);
    }
    if (url.startsWith("/api/documents")) {
      return jsonResponse(overrides.documents ?? { documents: [], total: 0 });
    }

    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProjectStructurePanel />
    </QueryClientProvider>,
  );
}

/** Open a file in the pane, the way a user does: by clicking it in the tree. */
async function openFile(path: string): Promise<HTMLElement> {
  const row = await screen.findByTestId(`workspace-file-${path}`);
  fireEvent.click(within(row).getByRole("button"));
  return row;
}

/** The editing surface for whatever file is open. There is only ever one. */
function pane(): HTMLElement {
  return screen.getByTestId("guidance-pane-editor");
}

function kindPicker(): HTMLSelectElement {
  return screen.getByLabelText("What are you adding") as HTMLSelectElement;
}

function submitAdd() {
  fireEvent.click(within(screen.getByTestId("add-rule-form")).getByRole("button", { name: /Add/ }));
}

function posted(url: string) {
  return calls.filter((call) => call.url === url && call.init?.method === "POST");
}

describe("ProjectStructurePanel", () => {
  beforeEach(() => {
    calls = [];
    permissions.canManageProjects = true;
    permissions.isOwner = true;
    initialProject.id = "proj";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // -- one editing surface, and it is the pane ------------------------------

  it("puts the organization editor in the pane that shows the organization file", async () => {
    stubFetch();
    renderPanel();

    await openFile(ORG_FILE);

    const editor = pane();
    expect(within(editor).getByRole("heading", { name: "Your organization's rules" })).toBeInTheDocument();
    expect(
      within(editor).getByText("Applies to every project and every person."),
    ).toBeInTheDocument();
    // The rule itself, retirable, beside the text of the file it lives in.
    expect(
      within(editor).getByText("Answer in the language the question was asked in."),
    ).toBeInTheDocument();
    expect(within(editor).getByRole("button", { name: /Retire rule 1/ })).toBeEnabled();
    expect(within(editor).getByTestId("add-rule-form")).toBeInTheDocument();

    // And it is inside the file view, not a card somewhere below it.
    expect(screen.getByTestId("workspace-tree")).toContainElement(editor);
  });

  it("puts the project editor in the pane that shows the project file", async () => {
    stubFetch();
    renderPanel();

    await openFile(PROJECT_FILE);

    const editor = pane();
    expect(within(editor).getByRole("heading", { name: "This project's rules" })).toBeInTheDocument();
    expect(within(editor).getByText("Applies to everyone working on proj.")).toBeInTheDocument();
    expect(within(editor).getByText("Treat the 4PS interface as read-only.")).toBeInTheDocument();
  });

  it("puts the preferences form in the pane that shows the preferences file", async () => {
    stubFetch();
    renderPanel();

    await openFile(PERSON_FILE);

    const editor = pane();
    expect(within(editor).getByRole("heading", { name: "My preferences" })).toBeInTheDocument();
    expect(
      within(editor).getByText(/Only changes how answers come to you/i),
    ).toBeInTheDocument();
    expect(await within(editor).findByLabelText("Audience")).toBeInTheDocument();
  });

  it("offers nothing to edit on a file that nothing edits", async () => {
    stubFetch();
    renderPanel();

    // The assembled file opens by default and is read-only.
    await screen.findByTestId("workspace-viewer-content");
    expect(screen.queryByTestId("guidance-pane-editor")).toBeNull();
    expect(screen.queryByTestId("add-rule-form")).toBeNull();
    expect(
      screen.getByText(/only gathers together the rules listed below it/i),
    ).toBeInTheDocument();

    // A mounted document is changed by uploading it again, not here.
    await openFile(SKILL_FILE);
    expect(screen.queryByTestId("guidance-pane-editor")).toBeNull();
    expect(screen.queryByTestId("add-rule-form")).toBeNull();
    expect(
      screen.getByText(/Upload it again under a different role/i),
    ).toBeInTheDocument();
  });

  it("keeps every editing control inside the file pane", async () => {
    stubFetch({ proposedRules: [PROPOSED_RULE] });
    const { container } = renderPanel();

    await openFile(PROJECT_FILE);
    const tree = screen.getByTestId("workspace-tree");

    // Exactly one editor, one add form, and both inside the tree's pane.
    expect(screen.getAllByTestId("guidance-pane-editor")).toHaveLength(1);
    expect(screen.getAllByTestId("add-rule-form")).toHaveLength(1);
    for (const form of Array.from(container.querySelectorAll("form"))) {
      expect(tree).toContainElement(form as HTMLElement);
    }

    // The level cards and the standalone preview are gone, not relocated.
    expect(screen.queryByRole("heading", { name: "Organization" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Uploaded material" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Where this is kept/ })).toBeNull();
    expect(
      screen.queryByRole("button", {
        name: /Read the instructions as the assistant gets them/,
      }),
    ).toBeNull();
  });

  // -- one add form, whose first control is the kind -------------------------

  it("switches the add form's fields when the kind changes", async () => {
    stubFetch();
    renderPanel();

    await openFile(PROJECT_FILE);

    const picker = kindPicker();
    expect(Array.from(picker.options).map((option) => option.textContent)).toEqual([
      "An instruction it must follow",
      "What a word means here",
      "What counts as proof",
      "How answers should be written",
    ]);

    // Free text to begin with, with what belongs there and one instance.
    expect(within(pane()).getByLabelText("The instruction")).toBeInTheDocument();
    expect(
      within(pane()).getByText(/Facts about this landscape that should shape every answer/i),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Term")).toBeNull();

    fireEvent.change(picker, { target: { value: "vocabulary" } });
    expect(screen.getByLabelText("Term")).toBeInTheDocument();
    expect(screen.getByLabelText("What it means here")).toBeInTheDocument();
    expect(screen.queryByLabelText("The instruction")).toBeNull();

    fireEvent.change(picker, { target: { value: "evidence_rule" } });
    expect(screen.getByLabelText("Kind of evidence")).toBeInTheDocument();
    expect(screen.getByLabelText("How much it counts")).toBeInTheDocument();
    expect(screen.queryByLabelText("Term")).toBeNull();

    fireEvent.change(picker, { target: { value: "output_rule" } });
    expect(screen.getByLabelText("The writing rule")).toBeInTheDocument();

    // Still one form, whatever is selected in it.
    expect(screen.getAllByTestId("add-rule-form")).toHaveLength(1);
  });

  it("offers the organization file only the kinds it can hold", async () => {
    stubFetch();
    renderPanel();

    await openFile(ORG_FILE);
    expect(Array.from(kindPicker().options).map((option) => option.textContent)).toEqual([
      "An instruction it must follow",
      "How answers should be written",
    ]);
    expect(
      within(pane()).getByText(/Standing instructions that hold for every client/i),
    ).toBeInTheDocument();
  });

  // -- capability: everything that used to be reachable still is -------------

  it("writes every kind the project file can hold, from the one form", async () => {
    stubFetch({ projectRules: [] });
    renderPanel();

    await openFile(PROJECT_FILE);

    fireEvent.change(screen.getByLabelText("The instruction"), {
      target: { value: "Never propose writes to the 4PS interface." },
    });
    submitAdd();
    await waitFor(() => expect(posted("/api/guidance/projects/proj")).toHaveLength(1));
    expect(JSON.parse(String(posted("/api/guidance/projects/proj")[0].init?.body))).toEqual({
      kind: "rule",
      content: "Never propose writes to the 4PS interface.",
    });

    fireEvent.change(kindPicker(), { target: { value: "vocabulary" } });
    fireEvent.change(screen.getByLabelText("Term"), { target: { value: "Koppelvlak" } });
    fireEvent.change(screen.getByLabelText("What it means here"), {
      target: { value: "One integration point between two systems, in one direction." },
    });
    submitAdd();
    await waitFor(() => expect(posted("/api/guidance/projects/proj")).toHaveLength(2));
    expect(JSON.parse(String(posted("/api/guidance/projects/proj")[1].init?.body))).toEqual({
      kind: "vocabulary",
      payload: {
        term: "Koppelvlak",
        aliases: [],
        description: "One integration point between two systems, in one direction.",
      },
    });

    fireEvent.change(kindPicker(), { target: { value: "evidence_rule" } });
    fireEvent.change(screen.getByLabelText("Kind of evidence"), {
      target: { value: "runtime_log" },
    });
    submitAdd();
    await waitFor(() => expect(posted("/api/guidance/projects/proj")).toHaveLength(3));
    expect(JSON.parse(String(posted("/api/guidance/projects/proj")[2].init?.body))).toEqual({
      kind: "evidence_rule",
      payload: { evidence_type: "runtime_log", weight: 50, source_kind: null },
    });

    fireEvent.change(kindPicker(), { target: { value: "output_rule" } });
    fireEvent.change(screen.getByLabelText("The writing rule"), {
      target: { value: "Always name the job and the environment." },
    });
    submitAdd();
    await waitFor(() => expect(posted("/api/guidance/projects/proj")).toHaveLength(4));
    expect(JSON.parse(String(posted("/api/guidance/projects/proj")[3].init?.body))).toEqual({
      kind: "output_rule",
      content: "Always name the job and the environment.",
    });
  });

  it("writes and retires an organization rule and an organization writing rule", async () => {
    stubFetch();
    renderPanel();

    await openFile(ORG_FILE);

    fireEvent.change(screen.getByLabelText("The instruction"), {
      target: { value: "Answer in the language the question was asked in." },
    });
    submitAdd();
    await waitFor(() => expect(posted("/api/guidance/org")).toHaveLength(1));
    expect(JSON.parse(String(posted("/api/guidance/org")[0].init?.body))).toEqual({
      kind: "rule",
      content: "Answer in the language the question was asked in.",
    });

    fireEvent.change(kindPicker(), { target: { value: "output_rule" } });
    fireEvent.change(screen.getByLabelText("The writing rule"), {
      target: { value: "Lead with the answer, then the evidence behind it." },
    });
    submitAdd();
    await waitFor(() => expect(posted("/api/guidance/org")).toHaveLength(2));
    expect(JSON.parse(String(posted("/api/guidance/org")[1].init?.body))).toEqual({
      kind: "output_rule",
      content: "Lead with the answer, then the evidence behind it.",
    });

    fireEvent.click(within(pane()).getByRole("button", { name: /Retire rule 1/ }));
    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.url === "/api/guidance/org/1" && call.init?.method === "DELETE",
        ),
      ).toBe(true),
    );
  });

  it("retires a project rule from the pane", async () => {
    stubFetch();
    renderPanel();

    await openFile(PROJECT_FILE);
    fireEvent.click(within(pane()).getByRole("button", { name: /Retire rule 7/ }));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url === "/api/guidance/projects/proj/rules/7" &&
            call.init?.method === "DELETE",
        ),
      ).toBe(true),
    );
  });

  it("offers approval of a suggested rule to a manager", async () => {
    stubFetch({ proposedRules: [PROPOSED_RULE] });
    renderPanel();

    await openFile(PROJECT_FILE);
    const editor = pane();

    expect(
      within(editor).getByRole("heading", { name: "Waiting for your approval (1)" }),
    ).toBeInTheDocument();
    const row = within(editor).getByTestId("guidance-rule-9");
    expect(row).toHaveAttribute("data-status", "proposed");
    expect(within(row).getByText("Waiting for approval")).toBeInTheDocument();
    expect(within(row).getByText("Suggested from a conversation")).toBeInTheDocument();
    expect(within(row).getByText(/Not being used yet/i)).toBeInTheDocument();

    const approve = within(row).getByRole("button", { name: /Approve rule 9/ });
    expect(approve).toBeEnabled();
    fireEvent.click(approve);

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url === "/api/guidance/projects/proj/rules/9/approve" &&
            call.init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("shows a suggested rule to a member but refuses the approval", async () => {
    permissions.canManageProjects = false;
    permissions.isOwner = false;
    stubFetch({ proposedRules: [PROPOSED_RULE] });
    renderPanel();

    await openFile(PROJECT_FILE);
    const approve = within(pane()).getByRole("button", { name: /Approve rule 9/ });
    expect(approve).toBeDisabled();
    expect(approve).toHaveAttribute("title", expect.stringContaining("manage projects"));
  });

  it("offers preferences as fixed choices and never as free text", async () => {
    stubFetch();
    renderPanel();

    await openFile(PERSON_FILE);
    const editor = await waitFor(() => {
      const node = pane();
      within(node).getByLabelText("Audience");
      return node;
    });

    // This level is a closed set by contract: the server rejects free text
    // here, so a text box would be an offer it refuses.
    expect(editor.querySelectorAll('input[type="text"]')).toHaveLength(0);
    expect(editor.querySelectorAll("input:not([type])")).toHaveLength(0);
    expect(editor.querySelectorAll("textarea")).toHaveLength(0);

    const audience = within(editor).getByLabelText("Audience") as HTMLSelectElement;
    expect(Array.from(audience.options).map((option) => option.value)).toEqual([
      "",
      "executive",
      "business",
      "engineering",
      "operations",
    ]);
    expect(within(editor).getByLabelText("Tone")).toBeInTheDocument();
    expect(within(editor).getByLabelText("Technical depth")).toBeInTheDocument();
    expect(within(editor).getByLabelText("Answer organization")).toBeInTheDocument();
  });

  it("saves the chosen preferences to the caller's own rows", async () => {
    stubFetch();
    renderPanel();

    await openFile(PERSON_FILE);
    const audience = (await screen.findByLabelText("Audience")) as HTMLSelectElement;
    fireEvent.change(audience, { target: { value: "engineering" } });
    fireEvent.click(screen.getByRole("button", { name: /Save preferences/ }));

    await waitFor(() => {
      const put = calls.find(
        (call) =>
          call.url === "/api/guidance/projects/proj/preferences" &&
          call.init?.method === "PUT",
      );
      expect(put).toBeDefined();
      expect(JSON.parse(String(put?.init?.body))).toMatchObject({
        audience: "engineering",
        formatting: [],
        communication: [],
      });
    });
  });

  it("lists the uploaded material, and what each file became, on the index", async () => {
    stubFetch({
      documents: {
        documents: [
          {
            id: 52,
            filename: "interface-standard.pdf",
            project_id: "proj",
            document_role: "standard",
            file_type: "pdf",
            chunk_count: 3,
            uploaded_at: "2026-09-01T10:00:00Z",
          },
        ],
        total: 1,
      },
    });
    renderPanel();

    await openFile(MANIFEST_FILE);

    const material = await screen.findByTestId("guidance-pane-material");
    expect(await within(material).findByText("interface-standard.pdf")).toBeInTheDocument();
    expect(within(material).getByText("Rules the assistant must follow")).toBeInTheDocument();
    expect(within(material).getByText(/uploaded as standard/)).toBeInTheDocument();
    // It is a listing, not an editor: the upload panel is the only way to change it.
    expect(screen.queryByTestId("guidance-pane-editor")).toBeNull();
    expect(
      screen.getByText(/upload a file again under a different role/i),
    ).toBeInTheDocument();
  });

  it("reads the assembled instructions, and says when two of them disagree", async () => {
    stubFetch({
      preview: {
        ...PREVIEW,
        conflicts: ["organization requires English; project requires Dutch"],
      },
    });
    renderPanel();

    // AGENTS.md opens first, so the merged text is the landing view.
    expect(await screen.findByTestId("workspace-viewer-content")).toHaveTextContent(
      "The assembled stack.",
    );
    const notes = screen.getByTestId("guidance-pane-notes");
    expect(
      within(notes).getByText(/Two of these instructions pull in different directions/i),
    ).toBeInTheDocument();
    expect(
      within(notes).getByText("organization requires English; project requires Dutch"),
    ).toBeInTheDocument();
    expect(
      within(notes).getByText(/includes your personal preferences and nobody else/i),
    ).toBeInTheDocument();
  });

  // -- refusals -------------------------------------------------------------

  it("turns a 409 into 'no room left here', beside the form that caused it", async () => {
    stubFetch({
      projectRules: [],
      createProjectRule: () =>
        jsonResponse(
          {
            error:
              "project guidance would exceed its 600 token budget: 640 > 600 (over by 40)",
            code: "guidance_budget_exceeded",
            retryable: false,
          },
          409,
        ),
    });
    renderPanel();

    await openFile(PROJECT_FILE);
    fireEvent.change(screen.getByLabelText("The instruction"), {
      target: { value: "One rule too many." },
    });
    submitAdd();

    const editor = await waitFor(() => {
      const node = pane();
      within(node).getByText(/No room left here/i);
      return node;
    });
    // The server's own sentence, verbatim, next to the box it refused.
    expect(
      within(editor).getByText(/project guidance would exceed its 600 token budget/i),
    ).toBeInTheDocument();
  });

  it("disables the project controls, with the reason, for a member without the permission", async () => {
    permissions.canManageProjects = false;
    permissions.isOwner = false;
    stubFetch();
    renderPanel();

    await openFile(PROJECT_FILE);
    const editor = pane();

    expect(within(editor).getByLabelText("The instruction")).toBeDisabled();
    expect(kindPicker()).toBeDisabled();

    const submit = within(editor).getByRole("button", { name: /Add/ });
    expect(submit).toBeDisabled();
    expect(submit).toHaveAttribute("title", expect.stringContaining("manage projects"));

    // Stated once in the pane as well, so the reason is not hover-only.
    expect(
      within(editor).getByText(/You can read this, but not change it/i),
    ).toBeInTheDocument();

    // Stopping an existing rule is refused for the same reason, and is visible
    // rather than hidden so the capability can be asked for.
    const retire = within(editor).getByRole("button", { name: /Retire rule 7/ });
    expect(retire).toBeDisabled();
    expect(retire).toHaveAttribute("title", expect.stringContaining("manage projects"));
  });

  it("disables the organization controls, with the reason, for a non-owner", async () => {
    permissions.isOwner = false;
    stubFetch();
    renderPanel();

    await openFile(ORG_FILE);
    const editor = pane();

    expect(within(editor).getByLabelText("The instruction")).toBeDisabled();
    const submit = within(editor).getByRole("button", { name: /Add/ });
    expect(submit).toBeDisabled();
    expect(submit).toHaveAttribute("title", expect.stringContaining("deployment owner"));
    expect(within(editor).getByRole("button", { name: /Retire rule 1/ })).toBeDisabled();
  });

  it("reports a refused read in the pane instead of rendering the file as empty", async () => {
    // A project the caller cannot see answers 403, and "no rules" would read as
    // "this project has no instructions" -- a materially wrong answer to a
    // question about how the system behaves.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/api/docs/projects")) return jsonResponse(PROJECTS);
        if (url.startsWith("/api/guidance/org")) return jsonResponse({ rules: [ORG_RULE] });
        if (url.startsWith("/api/guidance/projects/proj?status=")) {
          return jsonResponse(
            { error: "No access to this project", code: "forbidden", retryable: false },
            403,
          );
        }
        if (url.startsWith("/api/guidance/projects/proj/preview")) return jsonResponse(PREVIEW);
        if (url.startsWith("/api/guidance/projects/proj/workspace")) {
          return jsonResponse(WORKSPACE);
        }
        if (url.startsWith("/api/guidance/projects/proj/preferences")) {
          return jsonResponse(PREFERENCES);
        }
        if (url.startsWith("/api/documents")) return jsonResponse({ documents: [], total: 0 });
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
      }),
    );
    renderPanel();

    await openFile(PROJECT_FILE);
    expect(await within(pane()).findByText("No access to this project")).toBeInTheDocument();

    // The organization file is unaffected and still reads.
    await openFile(ORG_FILE);
    expect(
      within(pane()).getByText("Answer in the language the question was asked in."),
    ).toBeInTheDocument();
  });

  it("marks nothing as an error before the user has done anything", async () => {
    stubFetch();
    renderPanel();

    await openFile(PROJECT_FILE);
    await screen.findByLabelText("The instruction");

    // An untouched, empty box is not a mistake. The panel used to open with a
    // red "Type the rule first." over a blank textarea.
    expect(screen.queryByText(/Write the rule before adding it/i)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("complains only once Add is pressed, and stops as soon as you type", async () => {
    stubFetch();
    renderPanel();

    await openFile(PROJECT_FILE);
    const textarea = await screen.findByLabelText("The instruction");

    submitAdd();
    expect(await screen.findByText(/Write the rule before adding it/i)).toBeInTheDocument();

    fireEvent.change(textarea, { target: { value: "N" } });
    expect(screen.queryByText(/Write the rule before adding it/i)).toBeNull();
  });

  it("leaving an untouched field raises nothing, but a bad entry does", async () => {
    stubFetch();
    renderPanel();

    await openFile(PROJECT_FILE);
    fireEvent.change(kindPicker(), { target: { value: "vocabulary" } });

    const term = screen.getByLabelText("Term");
    fireEvent.blur(term);
    expect(screen.queryByText(/A term needs both a name and a description/i)).toBeNull();

    fireEvent.change(term, { target: { value: "Koppelvlak" } });
    fireEvent.blur(term);
    // A name with no description cannot be saved, and the user is told now
    // rather than after a round trip.
    expect(
      await screen.findByText(/A term needs both a name and a description/i),
    ).toBeInTheDocument();
  });

  // -- comprehension --------------------------------------------------------

  it("teaches an empty file what belongs in it", async () => {
    stubFetch({ orgRules: [], projectRules: [] });
    renderPanel();

    await openFile(PROJECT_FILE);
    expect(await within(pane()).findByTestId("pane-rules-empty")).toHaveTextContent(
      /What is true about this client's landscape/i,
    );
    expect(
      within(pane()).getByText(/"The 4PS interface is read-only/),
    ).toBeInTheDocument();

    await openFile(ORG_FILE);
    expect(within(pane()).getByTestId("pane-rules-empty")).toHaveTextContent(
      /House rules that hold no matter which client/i,
    );
  });

  it("reports the room left in the file being edited, in words", async () => {
    stubFetch();
    renderPanel();

    await openFile(ORG_FILE);
    // 128 of 400 leaves plenty; the numbers are the second line.
    expect(screen.getByText("Room to spare - about 68% of the space left")).toBeInTheDocument();
    expect(screen.getByText("128 of 400 tokens")).toBeInTheDocument();

    await openFile(PROJECT_FILE);
    expect(screen.getByText("Room to spare - about 89% of the space left")).toBeInTheDocument();

    // Nothing is stored at the person level, so there is no bar at all rather
    // than an empty one beside a ceiling nobody is near.
    await openFile(PERSON_FILE);
    expect(screen.queryByText(/of 150 tokens/)).toBeNull();
  });

  it("says a full file is full instead of drawing a bar and leaving it there", async () => {
    stubFetch({
      preview: { ...PREVIEW, layer_tokens: { org: 400, project: 64, person: 0 } },
    });
    renderPanel();

    await openFile(ORG_FILE);
    expect(
      screen.getByText("Full - retire something here before adding more"),
    ).toBeInTheDocument();
  });

  it("keeps the cost split and the per-question figure above the tree", async () => {
    stubFetch();
    renderPanel();

    expect(await screen.findByText("441 tokens")).toBeInTheDocument();
    expect(screen.getByText("sent with every question")).toBeInTheDocument();
    expect(screen.getByText(/5 sent with every question/)).toBeInTheDocument();
    expect(screen.getByText(/1 opened only when needed/)).toBeInTheDocument();
  });

  // -- the file that does not exist yet -------------------------------------

  it("lists a level file the server has not written, so the first entry can be added", async () => {
    stubFetch({
      projectRules: [],
      workspace: {
        ...WORKSPACE,
        files: WORKSPACE.files.filter(
          (file) => file.path !== PROJECT_FILE && file.path !== PERSON_FILE,
        ),
      },
    });
    renderPanel();

    // A level that renders empty is never projected by the backend. Without
    // this row there is no way to write the first project rule at all.
    const row = await screen.findByTestId(`workspace-file-${PROJECT_FILE}`);
    expect(row).toHaveAttribute("data-pending", "true");
    expect(within(row).getByText("Nothing in it yet")).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button"));
    expect(screen.getByText(/This file does not exist yet/i)).toBeInTheDocument();
    expect(within(pane()).getByTestId("add-rule-form")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("The instruction"), {
      target: { value: "The first rule this project has." },
    });
    submitAdd();
    await waitFor(() => expect(posted("/api/guidance/projects/proj")).toHaveLength(1));

    // And it is not counted as a cost in either direction, because it is not
    // a file yet.
    expect(screen.getByText(/3 sent with every question/)).toBeInTheDocument();
    expect(screen.getByText(/1 opened only when needed/)).toBeInTheDocument();
  });

  // -- the project selector -------------------------------------------------

  it("shows the file list once a project is chosen inside this panel", async () => {
    initialProject.id = "";
    stubFetch();
    renderPanel();

    expect(await screen.findByTestId("select-project-notice")).toHaveTextContent(
      "Select a project to see what the assistant is told about it.",
    );
    expect(screen.queryByTestId("workspace-tree")).toBeNull();

    // The picker lives here, so the panel is usable without leaving it.
    await screen.findByRole("option", { name: "Reverse engineer VW" });
    const picker = screen.getByLabelText("Project") as HTMLSelectElement;
    expect(Array.from(picker.options).map((option) => option.value)).toEqual([
      "",
      "proj",
      "other",
    ]);

    fireEvent.change(picker, { target: { value: "proj" } });

    const tree = await screen.findByTestId("workspace-tree");
    expect(within(tree).getByTestId("workspace-file-AGENTS.md")).toBeInTheDocument();
    expect(screen.queryByTestId("select-project-notice")).toBeNull();
  });

  it("uses exactly one empty state for 'no project chosen'", async () => {
    initialProject.id = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/docs/projects")) return jsonResponse(PROJECTS);
        if (url.startsWith("/api/guidance/org")) return jsonResponse({ rules: [ORG_RULE] });
        if (url.startsWith("/api/documents")) return jsonResponse({ documents: [], total: 0 });
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderPanel();

    expect(await screen.findAllByTestId("select-project-notice")).toHaveLength(1);
    // Nothing is rendered below the grid, so there is nothing to repeat the
    // instruction in a second or third wording.
    expect(screen.queryByTestId("guidance-pane-editor")).toBeNull();
    expect(screen.queryByTestId("add-rule-form")).toBeNull();
  });
});
