"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  listMultiRepoMembers,
  removeMultiRepoMember,
  syncMultiRepoProject,
  type MultiRepoMember,
  type MultiRepoSyncOutcome,
} from "@/lib/mcpClient";

interface Props {
  projectId: string;
  /** Called after membership changes so the parent can refetch the project. */
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
 * Without this the members were invisible: a project spanning ten repositories
 * showed a blank where a single-repo project shows its URL, with no way to see
 * per-repo ingest state, drop a repo, or pull one.
 *
 * "Sync" here is the manual equivalent of the push webhook, which is not yet
 * wired for multi-repo projects. Syncing one repository touches only that
 * repository's working tree, so the scan that follows sees unchanged bytes for
 * its siblings and only this repo's documentation is regenerated.
 */
export function MultiRepoMembersPanel({ projectId, onChanged }: Props) {
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
      const data = await listMultiRepoMembers(projectId);
      setMembers(data.members);
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

  async function sync(repoIdentity?: string) {
    if (repoIdentity) setBusyIdentity(repoIdentity);
    else setSyncingAll(true);
    setError(null);
    setOutcomes([]);
    try {
      const result = await syncMultiRepoProject(projectId, repoIdentity);
      setMembers(result.members);
      // Only report what actually moved or broke; "skipped" rows are noise when
      // a single repo was targeted, since every sibling is skipped by design.
      setOutcomes(
        result.sync.filter(
          (outcome) => outcome.changed || outcome.action === "failed",
        ),
      );
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyIdentity(null);
      setSyncingAll(false);
    }
  }

  async function remove(member: MultiRepoMember) {
    setBusyIdentity(member.repo_identity);
    setError(null);
    try {
      await removeMultiRepoMember(projectId, member.id);
      setConfirmRemoveId(null);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyIdentity(null);
    }
  }

  if (loading && members == null) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading member repositories&hellip;
      </p>
    );
  }

  // A single-repo project has no members; render nothing rather than an empty box.
  if (!error && (members == null || members.length === 0)) return null;

  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium">
            {members?.length ?? 0} member {members?.length === 1 ? "repository" : "repositories"}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Each member has its own documentation set under{" "}
            <code className="font-mono">{projectId}/&lt;name&gt;/docs</code>, plus a shared
            cross-repo view.
          </p>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          isLoading={syncingAll}
          disabled={busyIdentity !== null}
          onClick={() => sync()}
        >
          <RefreshCw className="h-3.5 w-3.5" /> Sync all
        </Button>
      </div>

      <ul className="mt-3 divide-y divide-border">
        {(members ?? []).map((member) => {
          const busy = busyIdentity === member.repo_identity;
          const confirming = confirmRemoveId === member.id;
          return (
            <li key={member.id} className="flex flex-wrap items-center gap-2 py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{member.subproject_name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {member.repo_identity}
                  {member.branch ? ` · ${member.branch}` : ""}
                  {" · "}
                  <span className="font-mono">{shortSha(member.last_ingested_commit_sha)}</span>
                  {" · "}
                  {formatWhen(member.last_ingested_at)}
                </p>
              </div>
              {confirming ? (
                <div className="flex shrink-0 items-center gap-2">
                  <span className="text-xs text-muted-foreground">
                    Remove {member.subproject_name}?
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmRemoveId(null)}
                  >
                    Keep
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    isLoading={busy}
                    onClick={() => remove(member)}
                  >
                    Remove
                  </Button>
                </div>
              ) : (
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    isLoading={busy && !syncingAll}
                    disabled={syncingAll || busyIdentity !== null}
                    onClick={() => sync(member.repo_identity)}
                  >
                    <RefreshCw className="h-3.5 w-3.5" /> Sync
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove ${member.subproject_name}`}
                    disabled={syncingAll || busyIdentity !== null}
                    onClick={() => setConfirmRemoveId(member.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {outcomes.length > 0 && (
        <ul className="mt-2 space-y-1">
          {outcomes.map((outcome) => (
            <li
              key={outcome.repo_identity}
              className={`flex items-start gap-1.5 text-xs ${
                outcome.action === "failed" ? "text-destructive" : "text-emerald-700"
              }`}
            >
              {outcome.action === "failed" ? (
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              ) : (
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              )}
              <span>
                {outcome.subproject_name}: {outcome.action}
                {outcome.error ? ` — ${outcome.error}` : ""}
                {outcome.changed && !outcome.error
                  ? " — regenerate to pick up the change"
                  : ""}
              </span>
            </li>
          ))}
        </ul>
      )}

      {outcomes.length === 0 && !syncingAll && busyIdentity === null && members != null && (
        <p className="mt-2 text-xs text-muted-foreground">
          Push-triggered updates are not wired for multi-repo projects yet; use Sync.
        </p>
      )}

      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}
