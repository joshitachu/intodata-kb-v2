"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileArchive, Loader2, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  addMultiRepoMembers,
  addZipSubProject,
  listAzureDevOpsRepos,
  listGithubAppInstallations,
  listGithubAppRepos,
  McpRequestError,
  type GithubAppInstallation,
  type MultiRepoMember,
} from "@/lib/mcpClient";
import { RepoMultiSelect } from "./RepoMultiSelect";
import {
  branchForMember,
  canonicalRepoKey,
  orderedSelection,
  toPickableAzureRepos,
  toPickableGithubRepos,
  type PickableRepo,
} from "./repoSelection";

interface Props {
  projectId: string;
  platform: string;
  /** "git" picks the repository path, "zip" the file-upload path. */
  sourceType: string;
  /** "azure_devops" routes the repo listing through the organization connection. */
  connectionKind?: string | null;
  azureDevOpsConnectionId?: string | null;
  /** Preselects the matching GitHub App installation when the project names one. */
  githubAccountLogin?: string | null;
  /** Existing members, so repositories already in this project are marked. */
  members: MultiRepoMember[];
  /**
   * Called after a sub-project is added; the panel refetches on this.
   * `addedNames` are the sub-project names the server reported, so the panel can
   * show them even when they are not git members and therefore not listable.
   */
  onAdded: (summary: string, addedNames: string[]) => void;
  onCancel: () => void;
}

/**
 * Add one more sub-project to a project that already has documentation.
 *
 * The point of this affordance is incremental cost: the next generation run
 * regenerates the new sub-project and reuses the rest, so growing a project is
 * not the same as rebuilding it. Nothing here scans or generates -- import,
 * scan and generate stay three separate deliberate steps, and the row's next
 * step becomes Scan once this succeeds.
 *
 * Two paths, chosen by the project's own source type rather than by asking:
 * a git project picks repositories from the same picker used at import time, and
 * a ZIP project uploads another archive. A project cannot be both.
 */
export function AddSubProjectPanel({
  projectId,
  platform,
  sourceType,
  connectionKind,
  azureDevOpsConnectionId,
  githubAccountLogin,
  members,
  onAdded,
  onCancel,
}: Props) {
  if (sourceType === "zip") {
    return <ZipSubProjectForm projectId={projectId} onAdded={onAdded} onCancel={onCancel} />;
  }
  return (
    <GitSubProjectForm
      projectId={projectId}
      platform={platform}
      connectionKind={connectionKind}
      azureDevOpsConnectionId={azureDevOpsConnectionId}
      githubAccountLogin={githubAccountLogin}
      members={members}
      onAdded={onAdded}
      onCancel={onCancel}
    />
  );
}

// --- ZIP ---------------------------------------------------------------------

function ZipSubProjectForm({
  projectId,
  onAdded,
  onCancel,
}: Pick<Props, "projectId" | "onAdded" | "onCancel">) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setProgress(0);
    try {
      const result = await addZipSubProject({
        projectId,
        file,
        onProgress: setProgress,
      });
      // The migration is worth reporting: on the first added sub-project the
      // project's original tree moves under a name of its own, and the operator
      // will see a sub-project they never created if we say nothing.
      onAdded(
        result.migrated_existing_source
          ? `Added ${result.subproject}. The existing source moved into its own sub-project, so both are now listed. Scan to pick up the new code.`
          : `Added ${result.subproject}. Scan to pick up the new code.`,
        [result.subproject],
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 space-y-3 rounded-md border border-dashed border-border p-3">
      <div>
        <p className="text-sm font-medium">Add a sub-project from a ZIP</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          The archive becomes its own sub-project with its own documentation set.
          What is already generated stays; the next run only pays for the new
          code.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={inputRef}
          type="file"
          accept=".zip"
          aria-label="Sub-project ZIP file"
          className="hidden"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setError(null);
          }}
        />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          <FileArchive className="h-3.5 w-3.5" /> Choose a ZIP
        </Button>
        <span className="min-w-0 truncate text-xs text-muted-foreground">
          {file ? file.name : "No file chosen"}
        </span>
      </div>

      {busy && progress > 0 && progress < 100 && (
        <p className="text-xs text-muted-foreground">Uploading… {progress}%</p>
      )}

      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" disabled={!file} isLoading={busy} onClick={submit}>
          <UploadCloud className="h-3.5 w-3.5" /> Add sub-project
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

// --- Git ---------------------------------------------------------------------

function GitSubProjectForm({
  projectId,
  platform,
  connectionKind,
  azureDevOpsConnectionId,
  githubAccountLogin,
  members,
  onAdded,
  onCancel,
}: Omit<Props, "sourceType">) {
  const isAzure = connectionKind === "azure_devops";
  const [repos, setRepos] = useState<PickableRepo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [branchOverrides, setBranchOverrides] = useState<Record<string, string>>({});
  const [installations, setInstallations] = useState<GithubAppInstallation[] | null>(null);
  const [installationId, setInstallationId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Repositories already in this project are marked rather than hidden: a repo
  // missing from the list looks like a permissions problem, whereas "already in
  // a project" explains itself.
  const connectedKeys = useMemo(
    () => new Set(members.map((member) => canonicalRepoKey(member.clone_url))),
    [members],
  );

  const loadAzureRepos = useCallback(async () => {
    if (!azureDevOpsConnectionId) {
      setError(
        "This project has no Azure DevOps organization connection, so its repositories cannot be listed.",
      );
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await listAzureDevOpsRepos(azureDevOpsConnectionId);
      setRepos(toPickableAzureRepos(data.repositories, data.organization_name, connectedKeys));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [azureDevOpsConnectionId, connectedKeys]);

  const loadGithubRepos = useCallback(
    async (id: number) => {
      setLoading(true);
      setError(null);
      try {
        const data = await listGithubAppRepos(id);
        setRepos(toPickableGithubRepos(data.repositories, connectedKeys));
      } catch (err) {
        if (err instanceof McpRequestError && err.status === 404) {
          setError(
            "That GitHub App installation no longer exists on GitHub. Reconnect the App, then try again.",
          );
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setLoading(false);
      }
    },
    [connectedKeys],
  );

  // Resolve which credential lists the repositories. The project row records the
  // account it was imported from but not the installation id, so the matching
  // installation is looked up by login; a single installation is used without
  // asking, and several unmatched ones are offered as a choice rather than
  // guessed at.
  useEffect(() => {
    if (isAzure) {
      void loadAzureRepos();
      return;
    }
    let cancelled = false;
    setLoading(true);
    listGithubAppInstallations()
      .then((list) => {
        if (cancelled) return;
        setInstallations(list);
        const matched = githubAccountLogin
          ? list.find((item) => item.account_login === githubAccountLogin)
          : undefined;
        const chosen = matched ?? (list.length === 1 ? list[0] : undefined);
        if (chosen) {
          setInstallationId(chosen.installation_id);
        } else {
          setLoading(false);
          if (list.length === 0) {
            setError(
              "No GitHub App installation is connected, so repositories cannot be listed.",
            );
          }
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [githubAccountLogin, isAzure, loadAzureRepos]);

  useEffect(() => {
    if (isAzure || installationId == null) return;
    void loadGithubRepos(installationId);
  }, [installationId, isAzure, loadGithubRepos]);

  const selectedRepos = useMemo(
    () => orderedSelection(repos, selectedKeys),
    [repos, selectedKeys],
  );

  async function submit() {
    if (selectedRepos.length === 0) {
      setError("Pick at least one repository.");
      return;
    }
    if (!isAzure && installationId == null) {
      setError("Choose a GitHub App installation first.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await addMultiRepoMembers({
        projectId,
        platform,
        provider: isAzure ? "azure_devops" : "github",
        installationId: isAzure ? undefined : (installationId ?? undefined),
        azureDevOpsConnectionId: isAzure ? (azureDevOpsConnectionId ?? undefined) : undefined,
        members: selectedRepos.map((repo) => ({
          repoIdentity: repo.identity,
          repoName: repo.name,
          cloneUrl: repo.cloneUrl,
          branch: branchForMember(repo, branchOverrides),
        })),
      });
      // A member that failed to clone is reported, not swallowed: the project
      // exists with the members that did clone and the rest can be retried.
      const failed = result.sync.filter((outcome) => outcome.action === "failed");
      setSelectedKeys(new Set());
      setBranchOverrides({});
      onAdded(
        failed.length > 0
          ? `Added ${selectedRepos.length - failed.length} of ${selectedRepos.length} repositories. Could not clone: ${failed
              .map((outcome) => outcome.subproject_name)
              .join(", ")}.`
          : `Added ${selectedRepos.length} ${
              selectedRepos.length === 1 ? "sub-project" : "sub-projects"
            }. Scan to pick up the new code.`,
        // Git members come back from the members endpoint on the refetch, so the
        // panel does not need to hold them; naming them here would duplicate the
        // rows it is about to render.
        [],
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const needsInstallationChoice =
    !isAzure && installationId == null && (installations?.length ?? 0) > 1;

  return (
    <div className="mt-3 space-y-3 rounded-md border border-dashed border-border p-3">
      <div>
        <p className="text-sm font-medium">Add a sub-project from a repository</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Each repository becomes its own sub-project with its own documentation
          set. What is already generated stays; the next run only pays for the new
          code.
        </p>
      </div>

      {needsInstallationChoice && (
        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground">
            Choose which GitHub App installation to list repositories from.
          </p>
          <div className="flex flex-wrap gap-2">
            {installations?.map((installation) => (
              <Button
                key={installation.installation_id}
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => setInstallationId(installation.installation_id)}
              >
                {installation.account_login ?? `installation ${installation.installation_id}`}
              </Button>
            ))}
          </div>
        </div>
      )}

      {loading ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading
          repositories&hellip;
        </p>
      ) : (
        !needsInstallationChoice && (
          <RepoMultiSelect
            repos={repos}
            selected={selectedKeys}
            onSelectedChange={(next) => {
              setSelectedKeys(next);
              setError(null);
            }}
            branchOverrides={branchOverrides}
            onBranchChange={(repoKey, branch) =>
              setBranchOverrides((current) => ({ ...current, [repoKey]: branch }))
            }
            onRefresh={
              isAzure
                ? () => void loadAzureRepos()
                : installationId != null
                  ? () => void loadGithubRepos(installationId)
                  : undefined
            }
            scopeHint={`Repositories that can be added to ${projectId}.`}
            emptyHint="No repository was returned. Grant access to the repository in the provider, then refresh."
          />
        )
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          disabled={selectedRepos.length === 0}
          isLoading={submitting}
          onClick={submit}
        >
          Add{" "}
          {selectedRepos.length > 1
            ? `${selectedRepos.length} sub-projects`
            : "sub-project"}
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={submitting} onClick={onCancel}>
          Cancel
        </Button>
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
