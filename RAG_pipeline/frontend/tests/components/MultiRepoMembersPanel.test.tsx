import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MultiRepoMembersPanel } from "@/app/settings/sources/MultiRepoMembersPanel";
import {
  listMultiRepoMembers,
  removeMultiRepoMember,
  syncMultiRepoProject,
} from "@/lib/mcpClient";

vi.mock("@/lib/mcpClient", async () => {
  const actual = await vi.importActual<typeof import("@/lib/mcpClient")>("@/lib/mcpClient");
  return {
    ...actual,
    listMultiRepoMembers: vi.fn(),
    removeMultiRepoMember: vi.fn(),
    syncMultiRepoProject: vi.fn(),
  };
});

// The add affordance has its own test file; here it must not reach the network.
vi.mock("@/app/settings/sources/AddSubProjectPanel", () => ({
  AddSubProjectPanel: () => <div data-testid="add-sub-project" />,
}));

/** The panel now needs the parent project's shape to pick an add path. */
function panelProps(over: Record<string, unknown> = {}) {
  return {
    projectId: "pf",
    platform: "talend",
    sourceType: "git",
    ...over,
  } as const;
}

function member(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    provider: "azure_devops",
    repo_identity: "acme/Talend/pplus_ais",
    repo_name: "pplus_ais",
    clone_url: "https://dev.azure.com/acme/Talend/_git/pplus_ais",
    branch: null,
    subproject_name: "pplus_ais",
    local_path: "/data/repos/pf/pplus_ais.__intodoc_repo__",
    last_ingested_commit_sha: "abcdef1234567890",
    last_ingested_at: "2026-08-27T09:00:00Z",
    enabled: true,
    ...over,
  };
}

const SECOND = member({
  id: "22222222-2222-2222-2222-222222222222",
  repo_identity: "acme/Talend/pplus_beheer",
  repo_name: "pplus_beheer",
  subproject_name: "pplus_beheer",
  last_ingested_commit_sha: null,
  last_ingested_at: null,
});

describe("MultiRepoMembersPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMultiRepoMembers).mockResolvedValue({
      project_id: "pf",
      members: [member(), SECOND] as never,
      sync: [],
    });
    vi.mocked(syncMultiRepoProject).mockResolvedValue({
      project_id: "pf",
      members: [member(), SECOND] as never,
      sync: [],
    });
    vi.mocked(removeMultiRepoMember).mockResolvedValue(undefined);
  });

  it("lists members with their per-repo ingest state", async () => {
    render(<MultiRepoMembersPanel {...panelProps()} />);

    await waitFor(() => expect(screen.getByText("2 sub-projects")).toBeInTheDocument());
    expect(screen.getByText("pplus_ais")).toBeInTheDocument();
    // The SHA is abbreviated, and a never-ingested member says so rather than
    // showing a blank.
    expect(screen.getByText(/abcdef1/)).toBeInTheDocument();
    expect(screen.getByText(/not scanned yet/)).toBeInTheDocument();
  });

  /**
   * The panel used to return null here. That is what made a single-source
   * project a dead end: there was nowhere to add a second sub-project, so the
   * only route was deleting the project and re-importing everything.
   */
  it("still renders for a single-source project, to host the add affordance", async () => {
    vi.mocked(listMultiRepoMembers).mockResolvedValue({
      project_id: "solo",
      members: [],
      sync: [],
    });
    render(<MultiRepoMembersPanel {...panelProps({ projectId: "solo" })} />);

    await waitFor(() =>
      expect(
        screen.getByText("One source, not split into sub-projects"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /Add sub-project/ })).toBeInTheDocument();
    // Nothing to sync when there are no members yet.
    expect(screen.queryByRole("button", { name: /Sync all/ })).not.toBeInTheDocument();
  });

  it("opens the add form on demand", async () => {
    render(<MultiRepoMembersPanel {...panelProps()} />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    expect(screen.queryByTestId("add-sub-project")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Add sub-project/ }));
    expect(screen.getByTestId("add-sub-project")).toBeInTheDocument();
  });

  it("syncs one repository, naming it so siblings are untouched", async () => {
    render(<MultiRepoMembersPanel {...panelProps()} />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: /^Sync$/ })[0]);

    await waitFor(() =>
      expect(syncMultiRepoProject).toHaveBeenCalledWith("pf", "acme/Talend/pplus_ais"),
    );
  });

  it("syncs every member when Sync all is used", async () => {
    render(<MultiRepoMembersPanel {...panelProps()} />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Sync all/ }));

    // No identity => every member.
    await waitFor(() => expect(syncMultiRepoProject).toHaveBeenCalledWith("pf", undefined));
  });

  it("reports what changed and stays quiet about untouched siblings", async () => {
    vi.mocked(syncMultiRepoProject).mockResolvedValue({
      project_id: "pf",
      members: [member(), SECOND] as never,
      sync: [
        {
          subproject_name: "pplus_ais",
          repo_identity: "acme/Talend/pplus_ais",
          action: "pulled",
          head_sha: "beef",
          previous_sha: "abcdef1234567890",
          changed: true,
          error: null,
        },
        {
          subproject_name: "pplus_beheer",
          repo_identity: "acme/Talend/pplus_beheer",
          action: "skipped",
          head_sha: null,
          previous_sha: null,
          changed: false,
          error: null,
        },
      ],
    });
    render(<MultiRepoMembersPanel {...panelProps()} />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Sync all/ }));

    await waitFor(() =>
      expect(screen.getByText(/pplus_ais: pulled/)).toBeInTheDocument(),
    );
    // A skipped sibling is the expected case and would be pure noise.
    expect(screen.queryByText(/pplus_beheer: skipped/)).not.toBeInTheDocument();
  });

  it("surfaces a failed member sync", async () => {
    vi.mocked(syncMultiRepoProject).mockResolvedValue({
      project_id: "pf",
      members: [member(), SECOND] as never,
      sync: [
        {
          subproject_name: "pplus_beheer",
          repo_identity: "acme/Talend/pplus_beheer",
          action: "failed",
          head_sha: null,
          previous_sha: null,
          changed: false,
          error: "not a fast-forward",
        },
      ],
    });
    render(<MultiRepoMembersPanel {...panelProps()} />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Sync all/ }));

    await waitFor(() =>
      expect(
        screen.getByText(/pplus_beheer: failed — not a fast-forward/),
      ).toBeInTheDocument(),
    );
  });

  it("confirms before removing a member", async () => {
    const onChanged = vi.fn();
    render(<MultiRepoMembersPanel {...panelProps()} onChanged={onChanged} />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Remove pplus_ais" }));
    // Nothing is deleted on the first click.
    expect(removeMultiRepoMember).not.toHaveBeenCalled();
    expect(screen.getByText("Remove pplus_ais?")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(removeMultiRepoMember).toHaveBeenCalledWith(
        "pf",
        "11111111-1111-1111-1111-111111111111",
      ),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("abandons a removal when Keep is chosen", async () => {
    render(<MultiRepoMembersPanel {...panelProps()} />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Remove pplus_ais" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));

    expect(removeMultiRepoMember).not.toHaveBeenCalled();
    expect(screen.queryByText("Remove pplus_ais?")).not.toBeInTheDocument();
  });

  it("shows a load failure instead of an empty panel", async () => {
    vi.mocked(listMultiRepoMembers).mockRejectedValue(new Error("members unavailable"));
    render(<MultiRepoMembersPanel {...panelProps()} />);

    await waitFor(() => expect(screen.getByText("members unavailable")).toBeInTheDocument());
  });

  it("says push-triggered updates are not wired yet", async () => {
    // Honesty over polish: without this the Sync button looks redundant.
    render(<MultiRepoMembersPanel {...panelProps()} />);
    await waitFor(() =>
      expect(
        screen.getByText(/Push-triggered updates are not wired for multi-repo projects yet/),
      ).toBeInTheDocument(),
    );
  });
});
