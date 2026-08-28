import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import RepoMultiSelect from "./RepoMultiSelect";
import {
  branchForMember,
  canonicalRepoKey,
  orderedSelection,
  toPickableAzureRepos,
  toPickableGithubRepos,
  type PickableRepo,
} from "../lib/repoSelection";
import type {
  AzureDevOpsConnection,
  AzureDevOpsRepo,
  GithubAppInstallation,
} from "../types";

interface Props {
  projectId: string;
  /** `git` or `zip`. Decides which route in is offered; nothing else branches. */
  sourceType: string;
  /** Sent with new git members so they are parsed like their siblings. */
  platform: string;
  /** Clone URLs already in this project, so the picker can say so. */
  connectedCloneUrls: string[];
  /**
   * Sub-project names the server reports as added, plus anything the operator
   * needs to know about how they were added (a migrated flat source tree).
   *
   * `requiresScan` comes from the server for the ZIP route rather than being
   * assumed: it is the server that knows whether the new source is already
   * readable by the existing scan cache, and claiming a scan is needed when it
   * is not sends the operator to a button they do not have to press.
   */
  onAdded: (result: {
    names: string[];
    note: string | null;
    requiresScan: boolean;
  }) => void;
  onCancel: () => void;
}

interface AppRepo {
  full_name: string | null;
  clone_url: string | null;
  private: boolean | null;
  default_branch: string | null;
}

/** Which credential the picker is listing repositories from. */
type GitSource =
  | { provider: "github"; installationId: number; label: string }
  | { provider: "azure_devops"; connection: AzureDevOpsConnection };

/**
 * Add a sub-project to a project that already exists.
 *
 * The two routes in mirror how the project was created, because they are the
 * only two the server accepts: a git project gains a member repository
 * (`POST /multi-repo/projects/{id}/members`, already additive -- existing
 * working trees are untouched and only the new clone is fetched), a ZIP project
 * gains an archive (`POST /projects/{id}/sources`).
 *
 * The repo picker is the same `RepoMultiSelect` the Add Repository dialog uses,
 * with one difference that matters: there is no post-install redirect to read an
 * installation id from here, so the credential is CHOSEN from the ones this user
 * already has rather than reconstructed from the URL. An operator adding a
 * sub-project a month later has no `?installation_id=` in the address bar.
 *
 * Nothing here scans. A new sub-project is source on disk that no scan has read
 * yet, and saying otherwise -- by chaining a scan, or by rendering the row as if
 * it were ready -- would hide a step the operator has to take deliberately.
 */
export default function AddSubprojectPanel({
  projectId,
  sourceType,
  platform,
  connectedCloneUrls,
  onAdded,
  onCancel,
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // --- ZIP route ---
  const [file, setFile] = useState<File | null>(null);

  // --- git route ---
  const [installations, setInstallations] = useState<GithubAppInstallation[] | null>(null);
  const [connections, setConnections] = useState<AzureDevOpsConnection[] | null>(null);
  const [source, setSource] = useState<GitSource | null>(null);
  const [appRepos, setAppRepos] = useState<AppRepo[]>([]);
  const [adoRepos, setAdoRepos] = useState<AzureDevOpsRepo[]>([]);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [branchOverrides, setBranchOverrides] = useState<Record<string, string>>({});

  const isZip = sourceType === "zip";

  // Both credential lanes are listed together: the operator is choosing WHERE
  // the repository lives, not which of Nexus's connection mechanisms to use.
  useEffect(() => {
    if (isZip) return;
    let cancelled = false;
    void (async () => {
      const [apps, ados] = await Promise.all([
        api.listGithubAppInstallations().catch(() => null),
        api.listAzureDevOpsConnections().catch(() => null),
      ]);
      if (cancelled) return;
      setInstallations(apps?.installations ?? []);
      setConnections(ados?.connections ?? []);
    })();
    return () => {
      cancelled = true;
    };
  }, [isZip]);

  const loadRepos = useCallback((next: GitSource) => {
    setSource(next);
    setSelectedKeys(new Set());
    setAppRepos([]);
    setAdoRepos([]);
    setLoadingRepos(true);
    setError(null);
    void (async () => {
      try {
        if (next.provider === "github") {
          const data = await api.listGithubAppRepos(next.installationId);
          setAppRepos(data.repositories ?? []);
        } else {
          const data = await api.listAzureDevOpsRepos(next.connection.id);
          setAdoRepos(data.repositories ?? []);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoadingRepos(false);
      }
    })();
  }, []);

  const connectedKeys = useMemo(
    () => new Set(connectedCloneUrls.map((url) => canonicalRepoKey(url))),
    [connectedCloneUrls],
  );

  const pickableRepos: PickableRepo[] = useMemo(() => {
    if (source?.provider === "github") return toPickableGithubRepos(appRepos, connectedKeys);
    if (source?.provider === "azure_devops") {
      return toPickableAzureRepos(
        adoRepos,
        source.connection.organization_name ?? "",
        connectedKeys,
      );
    }
    return [];
  }, [source, appRepos, adoRepos, connectedKeys]);

  const selectedRepos = useMemo(
    () => orderedSelection(pickableRepos, selectedKeys),
    [pickableRepos, selectedKeys],
  );

  async function submitZip() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.addProjectZipSource(projectId, file);
      onAdded({
        names: [result.subproject],
        requiresScan: result.requires_rescan,
        // Worth stating: the existing tree's documentation moves from
        // {project}/docs to {project}/{subproject}/docs, so links change.
        note: result.migrated_existing_source
          ? "The project's original source was moved into its own sub-project directory, so its documentation is now published under a sub-project name."
          : null,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitRepos() {
    if (!source || selectedRepos.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.addMultiRepoMembers(projectId, {
        platform,
        provider: source.provider,
        installation_id:
          source.provider === "github" ? source.installationId : undefined,
        azure_devops_connection_id:
          source.provider === "azure_devops" ? source.connection.id : undefined,
        members: selectedRepos.map((repo) => ({
          repo_identity: repo.identity,
          repo_name: repo.name,
          clone_url: repo.cloneUrl,
          branch: branchForMember(repo, branchOverrides),
        })),
      });
      // The server clones only what it actually added, so its sync report --
      // not the selection -- is the truth about what exists now.
      const outcomes = result.sync ?? [];
      const failed = outcomes.filter((outcome) => outcome.action === "failed");
      const added = outcomes
        .filter((outcome) => outcome.action !== "failed")
        .map((outcome) => outcome.subproject_name);
      if (failed.length > 0) {
        // Partial success is reported, not swallowed: the rows exist and each
        // failure can be retried with Sync.
        setError(
          `${failed.length} of ${outcomes.length} could not be cloned: ` +
            failed
              .map((outcome) => `${outcome.subproject_name} (${outcome.error ?? "no reason given"})`)
              .join(", "),
        );
        // A clone that landed is source on disk no scan has read yet.
        if (added.length > 0) onAdded({ names: added, note: null, requiresScan: true });
        return;
      }
      if (added.length === 0) {
        setError(
          "Nothing was added — every selected repository is already a sub-project of this project.",
        );
        return;
      }
      onAdded({ names: added, note: null, requiresScan: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const credentialsLoading = !isZip && (installations == null || connections == null);
  const credentialCount = (installations?.length ?? 0) + (connections?.length ?? 0);

  return (
    <div className="mt-3 rounded border border-white/[0.1] bg-black/20 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-300">Add sub-project</p>
          <p className="mt-0.5 text-xs text-slate-500">
            {isZip
              ? "The archive becomes its own sub-project. Existing sub-projects are left untouched; scan the project afterwards to read the new source."
              : "The repository becomes its own sub-project with its own documentation set. Existing clones are not re-fetched."}
          </p>
        </div>
        <button
          type="button"
          className="shrink-0 px-2 py-1 text-xs font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 transition-all"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>

      {isZip ? (
        <div className="mt-3 space-y-2">
          <input
            type="file"
            accept=".zip"
            aria-label="Sub-project ZIP file"
            className="w-full text-sm text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-surface-raised file:text-slate-300 hover:file:bg-[#1e2d4a]"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            className="px-3 py-1.5 text-sm font-medium bg-accent-primary text-surface-bg rounded hover:bg-accent-primary/90 disabled:opacity-40 transition-all"
            disabled={busy || !file}
            onClick={() => void submitZip()}
          >
            {busy ? "Uploading…" : "Add sub-project"}
          </button>
        </div>
      ) : credentialsLoading ? (
        <p className="mt-3 text-sm text-slate-500">Checking git connections…</p>
      ) : source == null ? (
        <div className="mt-3 space-y-2">
          {credentialCount === 0 ? (
            <p className="text-xs text-slate-400">
              No GitHub App installation or Azure DevOps connection is available to this
              account. Connect one from Add Repository, then add the sub-project here.
            </p>
          ) : (
            <>
              <p className="text-xs text-slate-500">
                Choose where the repository lives. Only repositories this credential can
                already read are listed.
              </p>
              <ul className="space-y-1">
                {(installations ?? []).map((installation) => (
                  <li key={`gh-${installation.installation_id}`}>
                    <button
                      type="button"
                      className="w-full px-2 py-1.5 text-left text-sm text-slate-300 border border-white/[0.1] rounded hover:border-white/[0.2] transition-all"
                      onClick={() =>
                        loadRepos({
                          provider: "github",
                          installationId: installation.installation_id,
                          label: installation.account_login ?? `installation ${installation.installation_id}`,
                        })
                      }
                    >
                      GitHub App · {installation.account_login ?? `installation ${installation.installation_id}`}
                    </button>
                  </li>
                ))}
                {(connections ?? []).map((connection) => (
                  <li key={`ado-${connection.id}`}>
                    <button
                      type="button"
                      className="w-full px-2 py-1.5 text-left text-sm text-slate-300 border border-white/[0.1] rounded hover:border-white/[0.2] transition-all"
                      onClick={() => loadRepos({ provider: "azure_devops", connection })}
                    >
                      Azure DevOps · {connection.organization_name}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          <RepoMultiSelect
            repos={pickableRepos}
            selected={selectedKeys}
            onSelectedChange={setSelectedKeys}
            branchOverrides={branchOverrides}
            onBranchChange={(repoKey, branch) =>
              setBranchOverrides((current) => ({ ...current, [repoKey]: branch }))
            }
            loading={loadingRepos}
            onRefresh={() => loadRepos(source)}
            scopeHint={
              source.provider === "github"
                ? `Repositories granted to this GitHub App installation (${source.label}).`
                : `Every repository this token can read in ${source.connection.organization_name}, across all Azure DevOps projects.`
            }
            emptyHint="This connection works, but returned no repositories. Grant access on the provider side, then refresh."
            actions={
              <button
                type="button"
                className="px-2 py-1 text-xs font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 transition-all"
                onClick={() => setSource(null)}
              >
                Change connection
              </button>
            }
          />
          <button
            type="button"
            className="px-3 py-1.5 text-sm font-medium bg-accent-primary text-surface-bg rounded hover:bg-accent-primary/90 disabled:opacity-40 transition-all"
            disabled={busy || selectedRepos.length === 0}
            onClick={() => void submitRepos()}
          >
            {busy
              ? "Adding…"
              : selectedRepos.length > 1
                ? `Add ${selectedRepos.length} sub-projects`
                : "Add sub-project"}
          </button>
        </div>
      )}

      {error && <p className="mt-2 text-sm text-red-400 font-mono break-all">{error}</p>}
    </div>
  );
}
