import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { MultiRepoMember, MultiRepoSyncOutcome } from "../types";

interface Props {
  projectId: string;
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
 * Member repositories of a multi-repo project.
 *
 * Mirrors the RAG frontend's `app/settings/sources/MultiRepoMembersPanel.tsx`.
 * Without it the members are invisible: `ProjectMetaGrid` renders a Repo URL row
 * only when `repo_url` is set, and a multi-repo project has none by definition.
 *
 * "Sync" is the manual equivalent of the push webhook, which is not yet wired
 * for multi-repo projects. Syncing one repository touches only that repository's
 * working tree, so the scan that follows sees unchanged bytes for its siblings
 * and only this repo's documentation is regenerated.
 */
export default function MultiRepoMembersPanel({ projectId, onChanged }: Props) {
  const [members, setMembers] = useState<MultiRepoMember[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyIdentity, setBusyIdentity] = useState<string | null>(null);
  const [syncingAll, setSyncingAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<MultiRepoSyncOutcome[]>([]);
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);

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

  // A single-repo project has no members; render nothing rather than an empty box.
  if (!error && (members == null || members.length === 0)) return null;

  const anyBusy = syncingAll || busyIdentity !== null;

  return (
    <div className="rounded border border-white/[0.07] bg-surface-raised p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-300">
            {members?.length ?? 0} member{" "}
            {members?.length === 1 ? "repository" : "repositories"}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            Each member has its own documentation set under{" "}
            <code className="font-mono">{projectId}/&lt;name&gt;/docs</code>, plus a shared
            cross-repo view.
          </p>
        </div>
        <button
          type="button"
          className="shrink-0 px-2 py-1 text-xs font-medium text-slate-300 border border-white/[0.1] rounded hover:border-white/[0.2] disabled:opacity-40 transition-all"
          disabled={anyBusy}
          onClick={() => sync()}
        >
          {syncingAll ? "Syncing…" : "Sync all"}
        </button>
      </div>

      <ul className="mt-3 divide-y divide-white/[0.05]">
        {(members ?? []).map((m) => {
          const busy = busyIdentity === m.repo_identity;
          const confirming = confirmRemoveId === m.id;
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

      {outcomes.length === 0 && !anyBusy && members != null && (
        <p className="mt-2 text-xs text-slate-500">
          Push-triggered updates are not wired for multi-repo projects yet; use Sync.
        </p>
      )}

      {error && <p className="mt-2 text-sm text-red-400 font-mono">{error}</p>}
    </div>
  );
}
