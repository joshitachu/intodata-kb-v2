/**
 * Provider-neutral repository selection for the multi-repo picker.
 *
 * GitHub and Azure DevOps return different shapes, but the picker only needs
 * five things per repo. Normalising here keeps `RepoMultiSelect` free of any
 * provider branch, and keeps the identity rules -- the part that must match the
 * backend exactly -- in one testable place.
 *
 * `identity` is the important field. It is the provider's own stable name for
 * the repository and becomes `repo_identity` on the server, which is the join
 * key a push event resolves against. It is NOT a display label and must never
 * be derived from one.
 */

export interface PickableRepo {
  /** React key + selection key. Stable per repo within one listing. */
  key: string;
  /** `repo_identity` sent to the server: "owner/name" | "org/project/repo". */
  identity: string;
  /** Leaf name, used to seed the sub-project name. */
  name: string;
  /** Group heading — the GitHub owner or the Azure DevOps project. */
  group: string;
  /** Credential-free clone URL. Empty when the provider omitted it. */
  cloneUrl: string;
  defaultBranch: string | null;
  /**
   * Set only when the repo CANNOT be used at all -- no clone URL, or disabled
   * by the provider. A hard block; the checkbox is disabled.
   */
  unavailableReason: string | null;
  /**
   * Informational, still selectable. "already connected" lives here rather than
   * in `unavailableReason`: one repository may legitimately belong to several
   * projects (the server's uniqueness is per project, and `find_by_identity`
   * returns a list for exactly this reason). A standalone `Talend8` project and
   * `Talend8` as a member of a wider domain project are both valid at once, so
   * blocking the second was wrong.
   */
  note: string | null;
}

export interface GithubRepoLike {
  full_name: string | null;
  clone_url: string | null;
  private?: boolean | null;
  default_branch?: string | null;
}

export interface AzureRepoLike {
  id?: string;
  name: string;
  project: string;
  clone_url?: string | null;
  default_branch?: string | null;
  is_disabled?: boolean;
}

export function canonicalRepoKey(url: string | null | undefined): string {
  return (url ?? "")
    .trim()
    .replace(/\/+$/g, "")
    .replace(/\.git$/i, "")
    .toLowerCase();
}

/** Owner segment of "owner/name"; the whole string when there is no slash. */
function githubOwner(fullName: string): string {
  const slash = fullName.indexOf("/");
  return slash > 0 ? fullName.slice(0, slash) : fullName;
}

function githubLeaf(fullName: string): string {
  const parts = fullName.split("/");
  return parts[parts.length - 1] || fullName;
}

export function toPickableGithubRepos(
  repos: GithubRepoLike[],
  connectedKeys: Set<string>,
): PickableRepo[] {
  return repos.map((repo) => {
    const cloneUrl = repo.clone_url ?? "";
    // full_name is GitHub's stable identity. Falling back to the clone URL keeps
    // a repo selectable if the field is ever absent, at the cost of a less
    // readable identity -- better than dropping it from the list silently.
    const identity = repo.full_name ?? canonicalRepoKey(cloneUrl);
    return {
      key: identity || cloneUrl,
      identity,
      name: repo.full_name ? githubLeaf(repo.full_name) : canonicalRepoKey(cloneUrl),
      group: repo.full_name ? githubOwner(repo.full_name) : "Repositories",
      cloneUrl,
      defaultBranch: repo.default_branch ?? null,
      unavailableReason: cloneUrl ? null : "no clone URL",
      note: connectedKeys.has(canonicalRepoKey(cloneUrl))
        ? "already in a project"
        : null,
    };
  });
}

export function toPickableAzureRepos(
  repos: AzureRepoLike[],
  organizationName: string,
  connectedKeys: Set<string>,
): PickableRepo[] {
  return repos.map((repo) => {
    const cloneUrl = repo.clone_url ?? "";
    // Azure DevOps repo names are unique only within a project, and projects
    // only within an organization, so all three segments are required for a
    // stable identity. The organization comes from the connection, not the repo.
    const identity = [organizationName, repo.project, repo.name]
      .filter(Boolean)
      .join("/");
    return {
      key: repo.id || identity,
      identity,
      name: repo.name,
      group: repo.project,
      cloneUrl,
      defaultBranch: repo.default_branch ?? null,
      unavailableReason: !cloneUrl
        ? "no clone URL"
        : repo.is_disabled
        ? "disabled in Azure DevOps"
        : null,
      note: connectedKeys.has(canonicalRepoKey(cloneUrl))
        ? "already in a project"
        : null,
    };
  });
}

export interface RepoGroup {
  group: string;
  repos: PickableRepo[];
}

/**
 * Group by owner/project and sort both levels, matching the order the server
 * lists repos in so the picker never appears to reshuffle between refreshes.
 */
export function groupRepos(repos: PickableRepo[]): RepoGroup[] {
  const byGroup = new Map<string, PickableRepo[]>();
  for (const repo of repos) {
    const bucket = byGroup.get(repo.group);
    if (bucket) bucket.push(repo);
    else byGroup.set(repo.group, [repo]);
  }
  return [...byGroup.entries()]
    .map(([group, items]) => ({
      group,
      repos: [...items].sort((a, b) => a.name.localeCompare(b.name)),
    }))
    .sort((a, b) => a.group.localeCompare(b.group));
}

/** Case-insensitive match on the group and the leaf name. */
export function filterRepos(repos: PickableRepo[], query: string): PickableRepo[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return repos;
  return repos.filter(
    (repo) =>
      repo.name.toLowerCase().includes(needle) ||
      repo.group.toLowerCase().includes(needle) ||
      repo.identity.toLowerCase().includes(needle),
  );
}

export function selectableRepos(repos: PickableRepo[]): PickableRepo[] {
  return repos.filter((repo) => repo.unavailableReason === null);
}

/**
 * The selected repos in the order the picker displays them.
 *
 * Order does not affect correctness -- members are independent and the server
 * orders partitions by name -- but taking the provider's listing order instead
 * would make the request, and every log line derived from it, disagree with
 * what the operator saw on screen.
 */
export function orderedSelection(
  repos: PickableRepo[],
  selected: Set<string>,
): PickableRepo[] {
  return repos
    .filter((repo) => selected.has(repo.key))
    .sort((a, b) => a.group.localeCompare(b.group) || a.name.localeCompare(b.name));
}

/**
 * Derive a project id from the selection.
 *
 * One repo keeps the historic behaviour (the repo's own name). Several repos
 * have no single obvious name, so the caller must supply one -- returning a
 * guess like the first repo's name would produce a project called `pplus_ais`
 * that actually contains ten repositories.
 */
export function suggestedProjectName(selected: PickableRepo[]): string {
  if (selected.length === 1) return selected[0].name;
  const groups = new Set(selected.map((repo) => repo.group));
  return groups.size === 1 ? [...groups][0] : "";
}

/**
 * The branch to pin a member to, or `undefined` to follow the default branch.
 *
 * An empty or whitespace override means "leave it unset", which is NOT the same
 * as sending the current default branch name: unset lets the clone follow
 * whatever the provider's default becomes, whereas a pinned name is frozen.
 * Only send what the operator actually typed.
 */
export function branchForMember(
  repo: PickableRepo,
  overrides: Record<string, string>,
): string | undefined {
  const raw = (overrides[repo.key] ?? "").trim();
  return raw || undefined;
}

/**
 * True when *projectId* would overwrite an existing project.
 *
 * `ProjectRegistry.add_project` UPDATES a project whose id already exists --
 * repointing its `local_path`, `repo_url`, `platform` and `source_type` -- so
 * reusing an id silently hijacks the other project rather than failing. The
 * picker must block this; it is the genuinely destructive collision, as opposed
 * to reusing a repository, which is legitimate.
 */
export function collidesWithExistingProject(
  projectId: string,
  existingProjectIds: Iterable<string>,
): boolean {
  const target = projectId.trim().toLowerCase();
  if (!target) return false;
  for (const existing of existingProjectIds) {
    if (existing.trim().toLowerCase() === target) return true;
  }
  return false;
}

export function slugifyProjectId(input: string): string {
  return input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}
