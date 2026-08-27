import { describe, expect, it } from "vitest";
import {
  canonicalRepoKey,
  filterRepos,
  groupRepos,
  orderedSelection,
  selectableRepos,
  slugifyProjectId,
  suggestedProjectName,
  toPickableAzureRepos,
  toPickableGithubRepos,
} from "@/app/settings/sources/repoSelection";

const NONE = new Set<string>();

describe("repo identity", () => {
  it("uses GitHub's full_name verbatim as the identity", () => {
    // The backend joins push events on this exact string, so it must be the
    // provider's own name and not a URL or a display label.
    const [repo] = toPickableGithubRepos(
      [{ full_name: "acme/etl", clone_url: "https://github.com/acme/etl.git" }],
      NONE,
    );
    expect(repo.identity).toBe("acme/etl");
    expect(repo.name).toBe("etl");
    expect(repo.group).toBe("acme");
  });

  it("builds an Azure DevOps identity from organization, project and repo", () => {
    // Repo names are unique only within a project, and projects only within an
    // organization, so all three segments are required.
    const [repo] = toPickableAzureRepos(
      [{ name: "etl", project: "Data", clone_url: "https://dev.azure.com/acme/Data/_git/etl" }],
      "acme",
      NONE,
    );
    expect(repo.identity).toBe("acme/Data/etl");
    expect(repo.name).toBe("etl");
    expect(repo.group).toBe("Data");
  });

  it("keeps two same-named repos in different Azure DevOps projects distinct", () => {
    const repos = toPickableAzureRepos(
      [
        { id: "1", name: "common", project: "Data", clone_url: "https://x/Data/_git/common" },
        { id: "2", name: "common", project: "Web", clone_url: "https://x/Web/_git/common" },
      ],
      "acme",
      NONE,
    );
    expect(new Set(repos.map((r) => r.identity)).size).toBe(2);
    expect(new Set(repos.map((r) => r.key)).size).toBe(2);
  });
});

describe("availability", () => {
  it("notes an already-connected repo but keeps it selectable", () => {
    // One repository may legitimately belong to several projects: the server's
    // uniqueness is per project, so a standalone Talend8 project and Talend8 as
    // a member of a wider domain project are both valid at once.
    // Trailing slash, .git suffix and case all differ from the stored value.
    const connected = new Set([canonicalRepoKey("https://dev.azure.com/acme/Data/_git/ETL/")]);
    const [repo] = toPickableAzureRepos(
      [{ name: "etl", project: "Data", clone_url: "https://dev.azure.com/acme/Data/_git/etl.git" }],
      "acme",
      connected,
    );
    expect(repo.note).toBe("already in a project");
    expect(repo.unavailableReason).toBeNull();
    expect(selectableRepos([repo])).toHaveLength(1);
  });

  it("keeps a hard-blocked repo out of the selectable set", () => {
    const [disabled] = toPickableAzureRepos(
      [{ name: "old", project: "Data", clone_url: "https://x/_git/old", is_disabled: true }],
      "acme",
      NONE,
    );
    expect(selectableRepos([disabled])).toEqual([]);
  });

  it("marks a repo disabled in Azure DevOps", () => {
    const [repo] = toPickableAzureRepos(
      [{ name: "old", project: "Data", clone_url: "https://x/_git/old", is_disabled: true }],
      "acme",
      NONE,
    );
    expect(repo.unavailableReason).toBe("disabled in Azure DevOps");
  });

  it("marks a repo with no clone URL rather than dropping it", () => {
    // Dropping it would look like a permissions problem to the operator.
    const [repo] = toPickableGithubRepos([{ full_name: "acme/x", clone_url: null }], NONE);
    expect(repo.unavailableReason).toBe("no clone URL");
  });
});

describe("grouping and filtering", () => {
  const repos = toPickableAzureRepos(
    [
      { id: "1", name: "pplus_ais", project: "Talend", clone_url: "https://x/1" },
      { id: "2", name: "JDE_DWH", project: "Talend", clone_url: "https://x/2" },
      { id: "3", name: "Integratie", project: "Enterprise_DWH", clone_url: "https://x/3" },
    ],
    "acme",
    NONE,
  );

  it("groups by project and sorts both levels", () => {
    const groups = groupRepos(repos);
    expect(groups.map((g) => g.group)).toEqual(["Enterprise_DWH", "Talend"]);
    expect(groups[1].repos.map((r) => r.name)).toEqual(["JDE_DWH", "pplus_ais"]);
  });

  it("filters on name, group and identity, case-insensitively", () => {
    expect(filterRepos(repos, "PPLUS").map((r) => r.name)).toEqual(["pplus_ais"]);
    expect(filterRepos(repos, "enterprise").map((r) => r.name)).toEqual(["Integratie"]);
    expect(filterRepos(repos, "acme/Talend").length).toBe(2);
  });

  it("returns everything for an empty filter", () => {
    expect(filterRepos(repos, "   ").length).toBe(3);
  });
});

describe("selection order", () => {
  it("returns selected repos in display order, not provider order", () => {
    // The provider lists etl before api; the picker shows Data before Platform
    // and api before etl inside a group. The request must match the screen.
    const repos = toPickableAzureRepos(
      [
        { id: "1", name: "etl", project: "Platform", clone_url: "https://x/1" },
        { id: "2", name: "api", project: "Platform", clone_url: "https://x/2" },
        { id: "3", name: "dwh", project: "Data", clone_url: "https://x/3" },
      ],
      "acme",
      NONE,
    );
    const selected = new Set(repos.map((r) => r.key));
    expect(orderedSelection(repos, selected).map((r) => r.name)).toEqual([
      "dwh",
      "api",
      "etl",
    ]);
  });

  it("ignores keys that are not selected", () => {
    const repos = toPickableGithubRepos(
      [
        { full_name: "acme/a", clone_url: "https://x/a" },
        { full_name: "acme/b", clone_url: "https://x/b" },
      ],
      NONE,
    );
    expect(orderedSelection(repos, new Set(["acme/b"])).map((r) => r.name)).toEqual(["b"]);
  });
});

describe("project naming", () => {
  const [a, b] = toPickableAzureRepos(
    [
      { id: "1", name: "etl", project: "Data", clone_url: "https://x/1" },
      { id: "2", name: "api", project: "Platform", clone_url: "https://x/2" },
    ],
    "acme",
    NONE,
  );

  it("suggests the repo's own name for a single selection", () => {
    expect(suggestedProjectName([a])).toBe("etl");
  });

  it("suggests the shared group when every selected repo shares one", () => {
    const [x, y] = toPickableAzureRepos(
      [
        { id: "1", name: "pplus_ais", project: "Talend", clone_url: "https://x/1" },
        { id: "2", name: "pplus_beheer", project: "Talend", clone_url: "https://x/2" },
      ],
      "acme",
      NONE,
    );
    expect(suggestedProjectName([x, y])).toBe("Talend");
  });

  it("suggests nothing when selected repos span groups", () => {
    // Guessing here would label a multi-repo project after one of its members.
    expect(suggestedProjectName([a, b])).toBe("");
  });

  it("slugifies a name into a valid project id", () => {
    expect(slugifyProjectId(" Pluralfarm Group ")).toBe("pluralfarm-group");
    expect(slugifyProjectId("!!!")).toBe("");
  });
});
