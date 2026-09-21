import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { WorkspaceTree } from "@/components/guidance/WorkspaceTree";
import type { ProjectWorkspaceResponse, WorkspaceFile } from "@/types/guidance";

/**
 * The tree is the only honest answer to "what does the assistant actually
 * have", and now also the only place anything is changed.
 *
 * These tests pin the things that make it worth showing instead of a prose
 * restatement of the layout: that the real folder nesting survives, that "sent
 * with every question" is distinguishable from "opened only when needed"
 * without hovering anything, and that the per-question cost is stated in words
 * a reader can act on.
 *
 * They also pin the pane's second job. Whatever writes the selected file is
 * rendered under that file's text and nowhere else, so selecting a different
 * file swaps the editor with it -- and a file nothing writes gets no editor at
 * all rather than a disabled one that implies it could be written.
 */

const ORG_FILE = ".nexus/org/RULES.md";
const PROJECT_FILE = ".nexus/project/RULES.md";
const SKILL_FILE = ".nexus/project/skills/52-test/SKILL.md";
const SYSTEM_PROMPT_FILE = ".nexus/SYSTEM_PROMPT.md";

const WORKSPACE: ProjectWorkspaceResponse = {
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
      path: SYSTEM_PROMPT_FILE,
      origin: "system_prompt",
      always_loaded: true,
      bytes: 4210,
      content: "You are Nexus, an integration-documentation analyst.",
    },
    {
      path: ORG_FILE,
      origin: "org_layer",
      always_loaded: true,
      bytes: 756,
      content: "- Answer in the language the question was asked in.",
    },
    {
      path: ".nexus/project/MANIFEST.md",
      origin: "manifest",
      always_loaded: false,
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

function renderTree(workspace: ProjectWorkspaceResponse = WORKSPACE) {
  return render(<WorkspaceTree workspace={workspace} isLoading={false} error={null} />);
}

function openFile(path: string) {
  fireEvent.click(within(screen.getByTestId(`workspace-file-${path}`)).getByRole("button"));
}

describe("WorkspaceTree", () => {
  it("nests files inside the folders their paths name", () => {
    renderTree();

    const nexus = screen.getByTestId("workspace-dir-.nexus");
    const project = within(nexus).getByTestId("workspace-dir-.nexus/project");
    const skills = within(project).getByTestId("workspace-dir-.nexus/project/skills");
    const skillDir = within(skills).getByTestId(
      "workspace-dir-.nexus/project/skills/52-test",
    );

    // The deepest file is reachable only through the whole chain, which is what
    // a flat list cannot express.
    expect(within(skillDir).getByTestId(`workspace-file-${SKILL_FILE}`)).toBeInTheDocument();

    // Only the leaf segment is shown; the tree carries the rest.
    expect(within(skills).getByText("52-test/")).toBeInTheDocument();
    expect(screen.getByText("SKILL.md")).toBeInTheDocument();

    // A root-level file is not swallowed by a folder.
    const agents = screen.getByTestId("workspace-file-AGENTS.md");
    expect(agents.closest('[data-testid="workspace-dir-.nexus"]')).toBeNull();
  });

  it("says in plain words which files ride along with every question", () => {
    renderTree();

    const agents = screen.getByTestId("workspace-file-AGENTS.md");
    const orgRules = screen.getByTestId(`workspace-file-${ORG_FILE}`);
    const skill = screen.getByTestId(`workspace-file-${SKILL_FILE}`);

    expect(agents).toHaveAttribute("data-always-loaded", "true");
    expect(orgRules).toHaveAttribute("data-always-loaded", "true");
    expect(skill).toHaveAttribute("data-always-loaded", "false");

    // Worded on the row itself -- no jargon, no tooltip needed, and it survives
    // a greyscale screenshot pasted into a ticket.
    expect(within(agents).getByText("Every question")).toBeInTheDocument();
    expect(within(skill).getByText("Only when needed")).toBeInTheDocument();

    // And carried in colour as well as in words.
    expect(within(agents).getByRole("button").className).toContain("border-amber-500");
    expect(within(skill).getByRole("button").className).toContain("border-transparent");
  });

  it("states the cost per question in tokens and in words", () => {
    renderTree();

    // The server's own figure, verbatim -- the other always-loaded files are
    // copies of sections of AGENTS.md, so summing them would double-count.
    expect(screen.getByText("441 tokens")).toBeInTheDocument();
    expect(screen.getByText("sent with every question")).toBeInTheDocument();
    // Translated into a unit a consultant already has a feel for.
    expect(screen.getByText(/roughly 331 words/i)).toBeInTheDocument();

    expect(screen.getByText(/3 sent with every question/)).toBeInTheDocument();
    expect(screen.getByText(/2 opened only when needed/)).toBeInTheDocument();
  });

  it("shows a file's content and where it came from when it is selected", () => {
    renderTree();

    // AGENTS.md opens by default; selecting another file swaps the viewer.
    expect(screen.getByTestId("workspace-viewer-content")).toHaveTextContent(
      "The assembled stack.",
    );

    openFile(SKILL_FILE);

    expect(screen.getByTestId("workspace-viewer-content")).toHaveTextContent(
      "How to run the regression pack.",
    );
    expect(screen.getByTestId("workspace-viewer-origin")).toHaveTextContent(
      "Uploaded document #52",
    );
    // The path is still there, as secondary detail rather than as the headline.
    expect(screen.getByTestId("workspace-viewer-path")).toHaveTextContent(SKILL_FILE);
  });

  it("names where a generated file came from without naming the machinery", () => {
    renderTree();

    openFile(ORG_FILE);
    expect(screen.getByTestId("workspace-viewer-origin")).toHaveTextContent(
      "Your organization's rules",
    );
  });

  it("explains the system role rather than showing its internal token", () => {
    // The backend has projected this file since the system prompt was given its
    // own origin; the frontend knew no label for it, so the one view whose whole
    // purpose is showing a user what their assistant was told rendered the raw
    // value `system_prompt` against the file with the most authority in the tree.
    renderTree();

    openFile(SYSTEM_PROMPT_FILE);
    const origin = screen.getByTestId("workspace-viewer-origin");
    expect(origin).toHaveTextContent(
      "What the assistant is told it is, before your question",
    );
    expect(origin.textContent).not.toContain("system_prompt");
  });

  it("marks a file that only repeats other files rather than adding to them", () => {
    renderTree();

    // AGENTS.md is assembled from the rule files below it. Presenting it as an
    // independent source would double-count the instructions in force.
    expect(screen.getByText(/Nothing new in this file/i)).toBeInTheDocument();
    expect(
      screen.getByText(/only gathers together the rules listed below it/i),
    ).toBeInTheDocument();

    openFile(".nexus/project/MANIFEST.md");
    expect(screen.getByText(/A short index of what is available/i)).toBeInTheDocument();
  });

  it("filters the tree down to one side of the cost split", () => {
    renderTree();

    fireEvent.click(screen.getByRole("button", { name: "Sent with every question" }));
    expect(screen.getByTestId("workspace-file-AGENTS.md")).toBeInTheDocument();
    expect(screen.queryByTestId(`workspace-file-${SKILL_FILE}`)).toBeNull();
    // A folder with nothing left in it is pruned rather than left empty.
    expect(screen.queryByTestId("workspace-dir-.nexus/project/skills")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Opened only when needed" }));
    expect(screen.queryByTestId("workspace-file-AGENTS.md")).toBeNull();
    expect(screen.getByTestId(`workspace-file-${SKILL_FILE}`)).toBeInTheDocument();
  });

  it("reports the generated documentation either way", () => {
    renderTree();
    expect(screen.getByTestId("workspace-docs-symlink")).toHaveTextContent(
      "the generated documentation for this project",
    );

    render(
      <WorkspaceTree
        workspace={{ ...WORKSPACE, docs_mounted: false }}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getAllByTestId("workspace-docs-symlink").at(-1)).toHaveTextContent(
      "Not available",
    );
  });

  it("handles loading, failure and a project with nothing in it yet", () => {
    const { rerender } = render(
      <WorkspaceTree workspace={undefined} isLoading error={null} />,
    );
    expect(screen.getByText(/Collecting the files for this project/i)).toBeInTheDocument();

    rerender(
      <WorkspaceTree
        workspace={undefined}
        isLoading={false}
        error={new Error("No access to this project")}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("No access to this project");

    rerender(
      <WorkspaceTree
        workspace={{ ...WORKSPACE, files: [], always_loaded_tokens: 0 }}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getByText(/There is nothing here yet/i)).toBeInTheDocument();
  });

  it("says when the listing carried no text for a file", () => {
    render(
      <WorkspaceTree
        workspace={{
          ...WORKSPACE,
          files: [
            { path: "AGENTS.md", origin: "assembled", always_loaded: true, bytes: 10, content: null },
          ],
        }}
        isLoading={false}
        error={null}
      />,
    );

    expect(
      screen.getByText(/text of this file was not sent to this page/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("workspace-viewer-content")).toBeNull();
  });

  // -- the pane is the editor -----------------------------------------------

  it("renders the editor for the selected file under that file's text", () => {
    render(
      <WorkspaceTree
        workspace={WORKSPACE}
        isLoading={false}
        error={null}
        fileEditor={(file: WorkspaceFile) =>
          file.path === ORG_FILE ? <p data-testid="editor">Organization editor</p> : null
        }
      />,
    );

    // AGENTS.md opens first and nothing writes it.
    expect(screen.queryByTestId("editor")).toBeNull();

    openFile(ORG_FILE);
    expect(screen.getByTestId("editor")).toBeInTheDocument();
    // Under the text, not beside it: reading the file and changing it are one
    // place, and the editor can never be about a different file than the text.
    expect(screen.getByTestId("workspace-viewer-content").compareDocumentPosition(
      screen.getByTestId("editor"),
    ) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // Selecting something else swaps it away again.
    openFile(SKILL_FILE);
    expect(screen.queryByTestId("editor")).toBeNull();
  });

  it("reports the room left in the selected file in its own header", () => {
    const budget = vi.fn((file: WorkspaceFile) =>
      file.path === ORG_FILE ? <span data-testid="budget">Room to spare</span> : null,
    );
    render(
      <WorkspaceTree
        workspace={WORKSPACE}
        isLoading={false}
        error={null}
        fileBudget={budget}
      />,
    );

    expect(screen.queryByTestId("budget")).toBeNull();
    openFile(ORG_FILE);
    expect(screen.getByTestId("budget")).toBeInTheDocument();
    expect(budget).toHaveBeenCalledWith(expect.objectContaining({ path: ORG_FILE }));
  });

  // -- a file that does not exist yet ---------------------------------------

  it("lists a file that has not been written, without charging for it", () => {
    const pendingFile: WorkspaceFile = {
      path: PROJECT_FILE,
      origin: "project_layer",
      always_loaded: false,
      bytes: 0,
      content: "",
    };
    render(
      <WorkspaceTree
        workspace={{ ...WORKSPACE, files: [...WORKSPACE.files, pendingFile] }}
        isLoading={false}
        error={null}
        pendingPaths={new Set([PROJECT_FILE])}
        fileEditor={() => <p data-testid="editor">Project editor</p>}
      />,
    );

    const row = screen.getByTestId(`workspace-file-${PROJECT_FILE}`);
    expect(row).toHaveAttribute("data-pending", "true");
    expect(within(row).getByText("Nothing in it yet")).toBeInTheDocument();

    // Not counted as a cost in either direction -- it is not a file yet.
    expect(screen.getByText(/3 sent with every question/)).toBeInTheDocument();
    expect(screen.getByText(/2 opened only when needed/)).toBeInTheDocument();

    // ...and it is not claimed by either cost filter.
    fireEvent.click(screen.getByRole("button", { name: "Opened only when needed" }));
    expect(screen.queryByTestId(`workspace-file-${PROJECT_FILE}`)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Sent with every question" }));
    expect(screen.queryByTestId(`workspace-file-${PROJECT_FILE}`)).toBeNull();

    // But it still opens, and still offers the editor that brings it into being.
    fireEvent.click(screen.getByRole("button", { name: "All files" }));
    openFile(PROJECT_FILE);
    expect(screen.getByText(/This file does not exist yet/i)).toBeInTheDocument();
    expect(screen.getByTestId("editor")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace-viewer-content")).toBeNull();
  });
});
