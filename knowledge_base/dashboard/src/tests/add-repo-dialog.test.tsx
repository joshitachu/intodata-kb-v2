import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AddRepoDialog from "../components/AddRepoDialog";

const addRepoMutate = vi.hoisted(() => vi.fn());

vi.mock("../hooks/useDiscovery", () => ({
  usePlatforms: () => ({ data: { platforms: [{ id: "talend", label: "Talend" }] } }),
}));

vi.mock("../hooks/useProjects", () => ({
  useAddRepo: () => ({ mutate: addRepoMutate, isPending: false, isError: false, error: null }),
  useUploadZip: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

vi.mock("../api/client", () => ({
  api: {
    listGithubAppRepos: vi.fn(),
    getGithubAppInstallUrl: vi.fn(),
  },
}));

/** Fill Project ID + the git URL, then return the credential input if it is rendered. */
function fillUrlTab(url: string) {
  fireEvent.change(screen.getByPlaceholderText("e.g. vaxxinova"), {
    target: { value: "demo" },
  });
  fireEvent.change(screen.getByPlaceholderText("https://github.com/... or /path/to/repo"), {
    target: { value: url },
  });
  return screen.queryByPlaceholderText("ghp_... / glpat-... / PAT");
}

describe("AddRepoDialog credential field", () => {
  beforeEach(() => {
    addRepoMutate.mockReset();
    render(<AddRepoDialog open onClose={() => {}} />);
  });

  it("offers a token for any https host, not just github.com", () => {
    expect(fillUrlTab("https://gitlab.example.com/team/repo.git")).not.toBeNull();
  });

  it("still offers a token for github.com", () => {
    expect(fillUrlTab("https://github.com/org/repo.git")).not.toBeNull();
  });

  it("hides the token for a local path", () => {
    expect(fillUrlTab("/srv/repos/local-checkout")).toBeNull();
  });

  it("hides the token for an SSH remote, which authenticates on a key", () => {
    expect(fillUrlTab("git@github.com:org/repo.git")).toBeNull();
  });

  it("sends the token for an https remote", () => {
    const token = fillUrlTab("https://gitlab.example.com/team/repo.git");
    fireEvent.change(token!, { target: { value: "glpat-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(addRepoMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        url: "https://gitlab.example.com/team/repo.git",
        project_id: "demo",
        github_token: "glpat-secret",
      }),
      expect.anything(),
    );
  });

  it("does not send a stale token once the URL is switched to a local path", () => {
    const token = fillUrlTab("https://github.com/org/repo.git");
    fireEvent.change(token!, { target: { value: "ghp_secret" } });
    fillUrlTab("/srv/repos/local-checkout");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(addRepoMutate).toHaveBeenCalledWith(
      expect.objectContaining({ github_token: undefined }),
      expect.anything(),
    );
  });
});
