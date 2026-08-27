import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MultiRepoMembersPanel from "../components/MultiRepoMembersPanel";

const listMultiRepoMembers = vi.hoisted(() => vi.fn());
const removeMultiRepoMember = vi.hoisted(() => vi.fn());
const syncMultiRepoProject = vi.hoisted(() => vi.fn());

vi.mock("../api/client", () => ({
  api: { listMultiRepoMembers, removeMultiRepoMember, syncMultiRepoProject },
}));

const MEMBER_A = {
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
};

const MEMBER_B = {
  ...MEMBER_A,
  id: "22222222-2222-2222-2222-222222222222",
  repo_identity: "acme/Talend/pplus_beheer",
  repo_name: "pplus_beheer",
  subproject_name: "pplus_beheer",
  last_ingested_commit_sha: null,
  last_ingested_at: null,
};

describe("MultiRepoMembersPanel (dashboard)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMultiRepoMembers.mockResolvedValue({
      project_id: "pf",
      members: [MEMBER_A, MEMBER_B],
      sync: [],
    });
    syncMultiRepoProject.mockResolvedValue({
      project_id: "pf",
      members: [MEMBER_A, MEMBER_B],
      sync: [],
    });
    removeMultiRepoMember.mockResolvedValue({ status: "ok" });
  });

  it("lists members with per-repo ingest state", async () => {
    render(<MultiRepoMembersPanel projectId="pf" />);

    await waitFor(() =>
      expect(screen.getByText("2 member repositories")).toBeInTheDocument(),
    );
    expect(screen.getByText("pplus_ais")).toBeInTheDocument();
    expect(screen.getByText(/abcdef1/)).toBeInTheDocument();
    // A never-ingested member says so rather than showing a blank.
    expect(screen.getByText(/not ingested/)).toBeInTheDocument();
  });

  it("renders nothing for a single-repo project", async () => {
    listMultiRepoMembers.mockResolvedValue({ project_id: "solo", members: [], sync: [] });
    const { container } = render(<MultiRepoMembersPanel projectId="solo" />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("syncs one repository by identity so siblings are untouched", async () => {
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: "Sync" })[0]);

    await waitFor(() =>
      expect(syncMultiRepoProject).toHaveBeenCalledWith("pf", "acme/Talend/pplus_ais"),
    );
  });

  it("syncs every member with Sync all", async () => {
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Sync all" }));

    await waitFor(() => expect(syncMultiRepoProject).toHaveBeenCalledWith("pf", undefined));
  });

  it("reports what changed and stays quiet about skipped siblings", async () => {
    syncMultiRepoProject.mockResolvedValue({
      project_id: "pf",
      members: [MEMBER_A, MEMBER_B],
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
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Sync all" }));

    await waitFor(() => expect(screen.getByText(/pplus_ais: pulled/)).toBeInTheDocument());
    expect(screen.queryByText(/pplus_beheer: skipped/)).not.toBeInTheDocument();
  });

  it("surfaces a failed member sync", async () => {
    syncMultiRepoProject.mockResolvedValue({
      project_id: "pf",
      members: [MEMBER_A, MEMBER_B],
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
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Sync all" }));

    await waitFor(() =>
      expect(screen.getByText(/pplus_beheer: failed — not a fast-forward/)).toBeInTheDocument(),
    );
  });

  it("confirms before removing a member", async () => {
    const onChanged = vi.fn();
    render(<MultiRepoMembersPanel projectId="pf" onChanged={onChanged} />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Remove pplus_ais" }));
    expect(removeMultiRepoMember).not.toHaveBeenCalled();

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
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Remove pplus_ais" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));

    expect(removeMultiRepoMember).not.toHaveBeenCalled();
    expect(screen.queryByText("Remove pplus_ais?")).not.toBeInTheDocument();
  });

  it("shows a load failure instead of an empty panel", async () => {
    listMultiRepoMembers.mockRejectedValue(new Error("members unavailable"));
    render(<MultiRepoMembersPanel projectId="pf" />);

    await waitFor(() => expect(screen.getByText("members unavailable")).toBeInTheDocument());
  });

  it("says push-triggered updates are not wired yet", async () => {
    // Honesty over polish: otherwise the Sync button looks redundant.
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() =>
      expect(
        screen.getByText(/Push-triggered updates are not wired for multi-repo projects yet/),
      ).toBeInTheDocument(),
    );
  });
});
