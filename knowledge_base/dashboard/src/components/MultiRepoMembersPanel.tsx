import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import AddSubprojectPanel from "./AddSubprojectPanel";
import type { MultiRepoMember, MultiRepoSyncOutcome } from "../types";

interface Props {
  projectId: string;
  /**
   * The project's `source_type` (`git` or `zip`). Decides which route in the
   * Add affordance offers -- the server accepts a member repository for one and
   * an archive for the other, and refuses the wrong one with a 400.
   */
  sourceType?: string;
  /** Sent with a new git member so it is parsed like its siblings. */
  platform?: string;
  /** Called after membership changes so the caller can refetch the project. */
  onChanged?: () => void;
}

function shortSha(sha: string | null): string {
  return sha ? sha.slice(0, 7) : "not ingested";
}

function formatWhen(value: string | null): string {
  if (!value) return "never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "never" : date.toLocaleString();
}

/**
 * Sub-projects of a project, and the way to add one.
 *
 * Mirrors the RAG frontend's `app/settings/sources/MultiRepoMembersPanel.tsx`.
 * Without it the members are invisible: `ProjectMetaGrid` renders a Repo URL row
 * only when `repo_url` is set, and a multi-repo project has none by definition.
 *
 * "Sync" is the manual equivalent of the push webhook, which is not yet wired
 * for multi-repo projects. Syncing one repository touches only that repository's
 * working tree, so the scan that follows sees unchanged bytes for its siblings
 * and only this repo's documentation is regenerated.
 *
 * It renders for a SINGLE-source project too, which it deliberately did not
 * before: it returned null on an empty member list, so the only surface that can
 * add a sub-project was hidden from exactly the projects that have none -- a
 * one-repo or one-ZIP project could never become a two-sub-project one. An empty
 * box is the smaller cost.
 */
export default function MultiRepoMembersPanel({
  projectId,
  sourceType = "git",
  platform = "talend",
  onChanged,
}: Props) {
  const [members, setMembers] = useState<MultiRepoMember[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyIdentity, setBusyIdentity] = useState<string | null>(null);
  const [syncingAll, setSyncingAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<MultiRepoSyncOutcome[]>([]);
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  // Sub-projects added since this panel was opened. Held here rather than read
  // back from the member row because a freshly cloned repository already has a
  // commit sha -- it looks ingested while no scan has read a byte of it. The
  // operator's next step is Scan, and only this state can say so.
  const [pendingScan, setPendingScan] = useState<string[]>([]);
  const [addedNames, setAddedNames] = useState<string[]>([]);
  const [addNote, setAddNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listMultiRepoMembers(projectId);
      setMembers(data.members ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const sync = async (repoIdentity?: string) => {
    if (repoIdentity) setBusyIdentity(repoIdentity);
    else setSyncingAll(true);
    setError(null);
    setOutcomes([]);
    try {
      const result = await api.syncMultiRepoProject(projectId, repoIdentity);
      setMembers(result.members ?? []);
      // Only what moved or broke: "skipped" siblings are skipped by design and
      // would be pure noise when one repository was targeted.
      setOutcomes(
        (result.sync ?? []).filter((o) => o.changed || o.action === "failed"),
      );
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyIdentity(null);
      setSyncingAll(false);
    }
  };

  const remove = async (memberToRemove: MultiRepoMember) => {
    setBusyIdentity(memberToRemove.repo_identity);
    setError(null);
    try {
      await api.removeMultiRepoMember(projectId, memberToRemove.id);
      setConfirmRemoveId(null);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyIdentity(null);
    }
  };

  if (loading && members == null) {
    return <p className="text-sm text-slate-500">Loading member repositories…</p>;
  }

  const anyBusy = syncingAll || busyIdentity !== null;
  const memberList = members ?? [];
  const isZip = sourceType === "zip";
  // Only git and ZIP projects can gain a sub-project; the server accepts
  // nothing else, so offering an Add button for another source type would be a
  // button that can only 400.
  const canAdd = isZip || sourceType === "git";

  return (
    <div className="rounded border border-white/[0.07] bg-surface-raised p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-300">
            {memberList.length > 0
              ? `${memberList.length} member ${memberList.length === 1 ? "repository" : "repositories"}`
              : isZip
                ? "Uploaded sources"
                : "No member repositories"}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {memberList.length > 0 || isZip ? (
              <>
                Each sub-project has its own documentation set under{" "}
                <code className="font-mono">{projectId}/&lt;name&gt;/docs</code>, plus a
                shared cross-project view.
                {isZip && memberList.length === 0 && (
                  <>
                    {" "}
                    A ZIP project&apos;s sub-projects are enumerated by the scan, not
                    listed here.
                  </>
                )}
              </>
            ) : (
              <>
                This project has a single source. Adding a sub-project gives it its own
                documentation set and its own generation budget.
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {canAdd && !adding && (
            <button
              type="button"
              className="px-2 py-1 text-xs font-medium text-accent-primary border border-accent-primary/40 rounded hover:bg-accent-primary/10 disabled:opacity-40 transition-all"
              disabled={anyBusy}
              onClick={() => {
                setAdding(true);
                setAddNote(null);
              }}
            >
              + Add sub-project
            </button>
          )}
          {memberList.length > 0 && (
            <button
              type="button"
              className="px-2 py-1 text-xs font-medium text-slate-300 border border-white/[0.1] rounded hover:border-white/[0.2] disabled:opacity-40 transition-all"
              disabled={anyBusy}
              onClick={() => sync()}
            >
              {syncingAll ? "Syncing…" : "Sync all"}
            </button>
          )}
        </div>
      </div>

      {adding && (
        <AddSubprojectPanel
          projectId={projectId}
          sourceType={sourceType}
          platform={platform}
          connectedCloneUrls={memberList.map((m) => m.clone_url)}
          onCancel={() => setAdding(false)}
          onAdded={async ({ names, note, requiresScan }) => {
            setAdding(false);
            setAddNote(note);
            setAddedNames((current) => [...current, ...names]);
            // Only the server can say a scan is unnecessary; the flag is
            // honoured rather than assumed so the panel never sends the
            // operator to a button they do not have to press.
            if (requiresScan) setPendingScan((current) => [...current, ...names]);
            await load();
            onChanged?.();
          }}
        />
      )}

      {addedNames.length > 0 && (
        <div className="mt-3 rounded border border-amber-400/30 bg-amber-400/5 p-2">
          <p className="text-xs text-amber-400">
            {pendingScan.length > 0 ? "Added, not scanned: " : "Added: "}
            <span className="font-mono">{addedNames.join(", ")}</span>
          </p>
          <p className="mt-0.5 text-xs text-slate-400">
            {pendingScan.length > 0
              ? "Run Scan, then Generate. The next generation run pays model cost for the new sub-project and reuses the documentation of every sub-project whose source did not change."
              : "Generate when ready. The run pays model cost for the new sub-project and reuses the documentation of every sub-project whose source did not change."}
          </p>
          {addNote && <p className="mt-1 text-xs text-amber-400">{addNote}</p>}
        </div>
      )}

      <ul className="mt-3 divide-y divide-white/[0.05]">
        {memberList.map((m) => {
          const busy = busyIdentity === m.repo_identity;
          const confirming = confirmRemoveId === m.id;
          const unscanned = pendingScan.includes(m.subproject_name);
          return (
            <li key={m.id} className="flex flex-wrap items-center gap-2 py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-slate-300">{m.subproject_name}</p>
                <p className="truncate text-xs text-slate-500">
                  {m.repo_identity}
                  {m.branch ? ` · ${m.branch}` : ""}
                  {" · "}
                  <span className="font-mono">{shortSha(m.last_ingested_commit_sha)}</span>
                  {" · "}
                  {formatWhen(m.last_ingested_at)}
                  {unscanned && <span className="text-amber-400"> · not scanned</span>}
                </p>
              </div>
              {confirming ? (
                <div className="flex shrink-0 items-center gap-2">
                  <span className="text-xs text-slate-400">
                    Remove {m.subproject_name}?
                  </span>
                  <button
                    type="button"
                    className="px-2 py-1 text-xs font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 transition-all"
                    onClick={() => setConfirmRemoveId(null)}
                  >
                    Keep
                  </button>
                  <button
                    type="button"
                    className="px-2 py-1 text-xs font-medium text-red-400 border border-red-400/40 rounded hover:bg-red-400/10 disabled:opacity-40 transition-all"
                    disabled={busy}
                    onClick={() => remove(m)}
                  >
                    Remove
                  </button>
                </div>
              ) : (
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    className="px-2 py-1 text-xs font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 disabled:opacity-40 transition-all"
                    disabled={anyBusy}
                    onClick={() => sync(m.repo_identity)}
                  >
                    {busy && !syncingAll ? "Syncing…" : "Sync"}
                  </button>
                  <button
                    type="button"
                    aria-label={`Remove ${m.subproject_name}`}
                    className="px-2 py-1 text-xs font-medium text-slate-500 border border-white/[0.1] rounded hover:text-red-400 disabled:opacity-40 transition-all"
                    disabled={anyBusy}
                    onClick={() => setConfirmRemoveId(m.id)}
                  >
                    ✕
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {outcomes.length > 0 && (
        <ul className="mt-2 space-y-1">
          {outcomes.map((o) => (
            <li
              key={o.repo_identity}
              className={`text-xs ${o.action === "failed" ? "text-red-400" : "text-emerald-400"}`}
            >
              {o.subproject_name}: {o.action}
              {o.error ? ` — ${o.error}` : ""}
              {o.changed && !o.error ? " — regenerate to pick up the change" : ""}
            </li>
          ))}
        </ul>
      )}

      {outcomes.length === 0 && !anyBusy && memberList.length > 0 && (
        <p className="mt-2 text-xs text-slate-500">
          Push-triggered updates are not wired for multi-repo projects yet; use Sync.
        </p>
      )}

      {error && <p className="mt-2 text-sm text-red-400 font-mono">{error}</p>}
    </div>
  );
}
