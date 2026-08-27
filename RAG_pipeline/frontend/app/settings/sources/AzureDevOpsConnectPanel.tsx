"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  ExternalLink,
  GitBranch,
  Loader2,
  RotateCw,
  Unplug,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  addAzureDevOpsSource,
  connectAzureDevOps,
  createMultiRepoProject,
  deleteAzureDevOpsConnection,
  listAzureDevOpsConnections,
  listAzureDevOpsRepos,
  McpRequestError,
  testAzureDevOpsConnection,
  type AzureDevOpsConnection,
  type AzureDevOpsRepo,
  type SourceProject,
} from "@/lib/mcpClient";
import { RepoMultiSelect } from "./RepoMultiSelect";
import {
  branchForMember,
  canonicalRepoKey,
  collidesWithExistingProject,
  orderedSelection,
  slugifyProjectId,
  suggestedProjectName,
  toPickableAzureRepos,
} from "./repoSelection";

interface PlatformOption {
  id: string;
  label: string;
}

interface Props {
  platforms: PlatformOption[];
  defaultPlatform: string;
  existingProjects: SourceProject[];
  onConnected: (project: SourceProject, platform: string) => void | Promise<void>;
}

const PAT_DOCS_URL =
  "https://learn.microsoft.com/azure/devops/organizations/accounts/use-personal-access-tokens-to-authenticate";

// Slug and repo-key normalisation live in ./repoSelection so this panel and the
// GitHub panel cannot drift apart on identity rules.

/**
 * Azure DevOps connect flow, deliberately shaped like the GitHub App panel:
 *
 *  1. Paste an organization URL + a Personal Access Token once. Azure DevOps has
 *     no App-style install, so a PAT is the only credential it offers; the server
 *     verifies it, encrypts it, and keeps it per *organization* rather than per
 *     repository.
 *  2. The panel lists every repository that token can see, across all projects.
 *  3. Pick one, name the project, and it registers + clones + scans on the same
 *     pipeline as a GitHub source.
 *
 * The token is write-only from the browser's perspective: it is posted once and
 * never returned by any endpoint.
 */
export function AzureDevOpsConnectPanel({
  platforms,
  defaultPlatform,
  existingProjects,
  onConnected,
}: Props) {
  const [connections, setConnections] = useState<AzureDevOpsConnection[] | null>(null);
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [organizationUrl, setOrganizationUrl] = useState("");
  const [pat, setPat] = useState("");
  const [connecting, setConnecting] = useState(false);

  const [activeConnection, setActiveConnection] = useState<AzureDevOpsConnection | null>(null);
  const [repos, setRepos] = useState<AzureDevOpsRepo[]>([]);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  // Per-repo branch pin, keyed by repo key. Absent or blank means "follow the
  // provider's default branch", which then tracks upstream changes to it.
  const [branchOverrides, setBranchOverrides] = useState<Record<string, string>>({});
  const [projectName, setProjectName] = useState("");
  // Whether the operator typed the name themselves. Re-suggesting on every
  // selection change would fight their typing; suggesting only while the field
  // is EMPTY is worse -- picking one repo fills it, and every repo added after
  // that leaves the project named after a single member.
  const [nameEdited, setNameEdited] = useState(false);
  const [platform, setPlatform] = useState(defaultPlatform);
  const [registering, setRegistering] = useState(false);
  const [busyConnectionId, setBusyConnectionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const connectedRepoKeys = useMemo(
    () => new Set(existingProjects.map((project) => canonicalRepoKey(project.repo_url))),
    [existingProjects],
  );
  const pickableRepos = useMemo(
    () =>
      toPickableAzureRepos(
        repos,
        activeConnection?.organization_name ?? "",
        connectedRepoKeys,
      ),
    [repos, activeConnection, connectedRepoKeys],
  );
  const selectedRepos = useMemo(
    () => orderedSelection(pickableRepos, selectedKeys),
    [pickableRepos, selectedKeys],
  );
  const hasConnections = Boolean(connections?.length);
  const connectionStatusUnavailable = connections == null && connectionError != null;

  const loadConnections = useCallback(async () => {
    setLoadingConnections(true);
    try {
      setConnections(await listAzureDevOpsConnections());
      setConnectionError(null);
    } catch (err) {
      setConnectionError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingConnections(false);
    }
  }, []);

  useEffect(() => {
    void loadConnections();
  }, [loadConnections]);

  const loadRepos = useCallback(
    (connection: AzureDevOpsConnection) => {
      let cancelled = false;
      setLoadingRepos(true);
      setError(null);
      listAzureDevOpsRepos(connection.id)
        .then((data) => {
          if (cancelled) return;
          setRepos(data.repositories);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          if (err instanceof McpRequestError && err.status === 403) {
            // The connection is gone or belongs to someone else — leave the picker
            // rather than showing an empty repo list under a live-looking header.
            setActiveConnection(null);
            setRepos([]);
            void loadConnections();
          }
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!cancelled) setLoadingRepos(false);
        });
      return () => {
        cancelled = true;
      };
    },
    [loadConnections],
  );

  useEffect(() => {
    if (!activeConnection) return;
    return loadRepos(activeConnection);
  }, [activeConnection, loadRepos]);

  async function submitConnection() {
    const url = organizationUrl.trim();
    const token = pat.trim();
    if (!url || !token) {
      setError("Organization URL and personal access token are required.");
      return;
    }
    setConnecting(true);
    setError(null);
    setNotice(null);
    try {
      const connection = await connectAzureDevOps(url, token);
      // Drop the token from component state as soon as the server has it.
      setPat("");
      setOrganizationUrl("");
      setFormOpen(false);
      await loadConnections();
      useConnection(connection);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setConnecting(false);
    }
  }

  function useConnection(connection: AzureDevOpsConnection) {
    setActiveConnection(connection);
    setRepos([]);
    setSelectedKeys(new Set());
    setBranchOverrides({});
    setProjectName("");
    setNameEdited(false);
    setError(null);
    setNotice(null);
  }

  function changeSelection(next: Set<string>) {
    setSelectedKeys(next);
    setError(null);
    // Seed the project name only while the user has not typed one. With several
    // repos selected there is no single obvious name, so the suggestion is the
    // shared Azure DevOps project when they all share one and blank otherwise --
    // never the first repo's name, which would label a ten-repo project after
    // one of its members.
    if (!nameEdited) {
      const selected = pickableRepos.filter((repo) => next.has(repo.key));
      const suggestion = suggestedProjectName(selected);
      // An empty suggestion clears the field on purpose: a stale name from an
      // earlier, smaller selection would silently label the whole project.
      setProjectName(suggestion ? slugifyProjectId(suggestion) : "");
    }
  }

  async function verifyConnection(connection: AzureDevOpsConnection) {
    setBusyConnectionId(connection.id);
    setError(null);
    setNotice(null);
    try {
      const verified = await testAzureDevOpsConnection(connection.id);
      setNotice(
        `${verified.organization_name} is reachable${
          verified.account_name ? ` as ${verified.account_name}` : ""
        }.`,
      );
      await loadConnections();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyConnectionId(null);
    }
  }

  async function disconnect(connection: AzureDevOpsConnection) {
    setBusyConnectionId(connection.id);
    setError(null);
    setNotice(null);
    try {
      await deleteAzureDevOpsConnection(connection.id);
      if (activeConnection?.id === connection.id) {
        setActiveConnection(null);
        setRepos([]);
      }
      await loadConnections();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyConnectionId(null);
    }
  }

  async function finishConnect() {
    if (!activeConnection || selectedRepos.length === 0) {
      setError("Pick at least one repository to connect.");
      return;
    }
    const projectId = slugifyProjectId(
      projectName.trim() || suggestedProjectName(selectedRepos),
    );
    if (!projectId) {
      setError(
        selectedRepos.length > 1
          ? "Name the project that will hold these repositories."
          : "Enter a valid project name.",
      );
      return;
    }
    // Reusing an existing project id is the destructive collision: the server's
    // add_project UPDATES a project whose id already exists, repointing its
    // local_path, repo_url and platform. Reusing a REPOSITORY is fine and
    // deliberately allowed; reusing an id silently hijacks another project.
    if (collidesWithExistingProject(projectId, existingProjects.map((p) => p.id))) {
      setError(
        `A project called "${projectId}" already exists. Pick another name — ` +
          "reusing it would repoint the existing project at this source.",
      );
      return;
    }
    setRegistering(true);
    setError(null);
    try {
      if (selectedRepos.length === 1) {
        // One repo stays on the single-repo path deliberately: it sets
        // `repo_url`, which is what the existing push-webhook match keys on.
        // Routing it through the multi-repo path would silently cost auto-update.
        const only = selectedRepos[0];
        // Forward the pin: /mcp/add-repo now takes a branch, and dropping it
        // here would silently clone the default while the UI showed a pin.
        const onlyBranch = branchForMember(only, branchOverrides);
        const project = await addAzureDevOpsSource({
          repoUrl: only.cloneUrl,
          projectId,
          platform,
          branch: onlyBranch,
          displayName: `${only.group} / ${only.name}`,
          azureDevOpsConnectionId: activeConnection.id,
        });
        resetPicker();
        await onConnected(project, platform);
        return;
      }

      const result = await createMultiRepoProject({
        projectId,
        displayName: projectName.trim() || projectId,
        platform,
        provider: "azure_devops",
        azureDevOpsConnectionId: activeConnection.id,
        members: selectedRepos.map((repo) => ({
          repoIdentity: repo.identity,
          repoName: repo.name,
          cloneUrl: repo.cloneUrl,
          branch: branchForMember(repo, branchOverrides),
        })),
      });
      const failed = result.sync.filter((outcome) => outcome.action === "failed");
      resetPicker();
      // Report a partial success rather than swallowing it: the project exists
      // with the members that cloned, and the rest can be retried per repo.
      if (failed.length > 0) {
        setNotice(
          `Created ${projectId} with ${result.members.length} repositories. ` +
            `${failed.length} could not be cloned: ` +
            failed.map((outcome) => outcome.subproject_name).join(", "),
        );
      }
      // Built explicitly rather than cast: the multi-repo endpoint returns
      // members, not a project row. `repo_url` is null by definition — no single
      // URL describes N members — and the parent refetches the real row anyway.
      const created: SourceProject = {
        id: result.project_id,
        display_name: projectName.trim() || projectId,
        repo_url: null,
        platform,
        source_type: "git",
        status: "registered",
      };
      await onConnected(created, platform);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRegistering(false);
    }
  }

  function resetPicker() {
    setActiveConnection(null);
    setRepos([]);
    setSelectedKeys(new Set());
    setBranchOverrides({});
    setProjectName("");
    setNameEdited(false);
  }

  // Repo-picker mode.
  if (activeConnection) {
    return (
      <Card className="rounded-none border-x-0 shadow-none">
        <CardHeader className="px-1">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <GitBranch className="h-4 w-4" aria-hidden />
            </span>
            <div>
              <h2 className="text-base font-semibold">Finish connecting</h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                Connected to {activeConnection.organization_name}. Choose one or more
                repositories &mdash; the token stays on the server.
              </p>
            </div>
          </div>
        </CardHeader>
        <CardBody className="space-y-3 px-1">
          <RepoMultiSelect
            repos={pickableRepos}
            selected={selectedKeys}
            onSelectedChange={changeSelection}
            branchOverrides={branchOverrides}
            onBranchChange={(repoKey, branch) =>
              setBranchOverrides((current) => ({ ...current, [repoKey]: branch }))
            }
            loading={loadingRepos}
            onRefresh={() => loadRepos(activeConnection)}
            scopeHint="Showing every repository this token can read, across all Azure DevOps projects."
            emptyHint="The connection works, but Azure DevOps returned no repositories. Check that the token has Code (Read) scope for this organization, then refresh."
          />
          {!loadingRepos && (
            <div className="grid gap-2 sm:grid-cols-2">
              <Input
                type="text"
                aria-label="Project name"
                placeholder={
                  selectedRepos.length > 1 ? "Name for all selected repos" : "Project name / ID"
                }
                value={projectName}
                onChange={(e) => {
                  setNameEdited(true);
                  setProjectName(e.target.value);
                }}
              />
              <select
                aria-label="Platform"
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
              >
                {platforms.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
          {notice && <p className="text-xs text-amber-700">{notice}</p>}
          <div className="flex justify-between">
            <Button variant="ghost" size="sm" onClick={resetPicker}>
              Cancel
            </Button>
            <Button
              size="sm"
              isLoading={registering}
              disabled={selectedRepos.length === 0}
              onClick={finishConnect}
            >
              <GitBranch className="h-3.5 w-3.5" />{" "}
              {selectedRepos.length > 1
                ? `Import ${selectedRepos.length} repositories`
                : "Import repository"}
            </Button>
          </div>
        </CardBody>
      </Card>
    );
  }

  // Entry mode.
  return (
    <Card className="rounded-none border-x-0 shadow-none">
      <CardBody className="space-y-3 px-1 py-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex min-w-0 gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <GitBranch className="h-5 w-5" aria-hidden />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold">Azure DevOps</h2>
                {loadingConnections && connections == null ? (
                  <Badge variant="muted">
                    <Loader2 className="h-3 w-3 animate-spin" /> Checking
                  </Badge>
                ) : connectionStatusUnavailable ? (
                  <Badge variant="muted">Status unavailable</Badge>
                ) : hasConnections ? (
                  <Badge variant="success">
                    <CheckCircle2 className="h-3 w-3" /> Connected
                  </Badge>
                ) : (
                  <Badge variant="muted">Not connected</Badge>
                )}
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                {connectionStatusUnavailable
                  ? "Nexus could not verify the Azure DevOps connection right now. Retry before connecting again."
                  : hasConnections
                  ? "Nexus can list this organization's repositories and clone them using the stored token."
                  : "Paste your organization URL and a personal access token once. Nexus then lists every repository the token can read."}
              </p>
            </div>
          </div>

          {/* When connections exist, the per-organization cards below carry the
              actions — a duplicate header button would be ambiguous with more
              than one organization connected. */}
          {hasConnections ? null : connectionStatusUnavailable ? (
            <Button
              size="md"
              variant="secondary"
              isLoading={loadingConnections}
              onClick={loadConnections}
            >
              <RotateCw className="h-4 w-4" /> Retry status
            </Button>
          ) : loadingConnections && connections == null ? (
            <Button size="md" variant="secondary" isLoading disabled>
              Checking connection
            </Button>
          ) : (
            !formOpen && (
              <Button size="md" onClick={() => setFormOpen(true)}>
                <GitBranch className="h-4 w-4" /> Connect Azure DevOps
              </Button>
            )
          )}
        </div>

        {formOpen && (
          <div className="space-y-3 rounded-md border border-border bg-muted/30 p-3">
            <div className="grid gap-2 sm:grid-cols-2">
              <Input
                type="text"
                placeholder="https://dev.azure.com/your-org"
                value={organizationUrl}
                onChange={(e) => {
                  setOrganizationUrl(e.target.value);
                  setError(null);
                }}
                hasError={Boolean(error)}
              />
              <Input
                type="password"
                placeholder="Personal access token"
                autoComplete="off"
                value={pat}
                onChange={(e) => {
                  setPat(e.target.value);
                  setError(null);
                }}
                hasError={Boolean(error)}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              An organization, project or repository URL all work. The token needs only{" "}
              <span className="font-medium">Code (Read)</span>; it is encrypted at rest and
              never sent to the browser or to a model.
            </p>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() =>
                  window.open(PAT_DOCS_URL, "_blank", "noopener,noreferrer")
                }
              >
                <ExternalLink className="h-3.5 w-3.5" /> How to create a token
              </Button>
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setFormOpen(false);
                    setPat("");
                    setError(null);
                  }}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  isLoading={connecting}
                  disabled={!organizationUrl.trim() || !pat.trim()}
                  onClick={submitConnection}
                >
                  Connect
                </Button>
              </div>
            </div>
          </div>
        )}

        {hasConnections && connections && (
          <div className="space-y-2 rounded-md border border-emerald-200 bg-emerald-50/70 p-3">
            {connections.map((connection) => (
              <div
                key={connection.id}
                className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-emerald-900">
                    {connection.organization_name}
                    {connection.account_name ? ` — ${connection.account_name}` : ""}
                  </p>
                  <p className="mt-0.5 truncate text-xs text-emerald-800">
                    {connection.organization_url}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => useConnection(connection)}
                  >
                    Choose repositories
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    isLoading={busyConnectionId === connection.id}
                    onClick={() => verifyConnection(connection)}
                  >
                    <RotateCw className="h-3.5 w-3.5" /> Test token
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    isLoading={busyConnectionId === connection.id}
                    onClick={() => disconnect(connection)}
                  >
                    <Unplug className="h-3.5 w-3.5" /> Disconnect
                  </Button>
                </div>
              </div>
            ))}
            {!formOpen && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setFormOpen(true)}
              >
                Connect another organization
              </Button>
            )}
          </div>
        )}

        {connectionError && connections != null && (
          <p className="text-xs text-amber-700">
            The last connection refresh failed. Keeping the previously verified
            connections.
          </p>
        )}
        {notice && <p className="text-xs text-emerald-700">{notice}</p>}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </CardBody>
    </Card>
  );
}
