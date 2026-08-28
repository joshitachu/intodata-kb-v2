"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  listMultiRepoMembers,
  removeMultiRepoMember,
  syncMultiRepoProject,
  type MultiRepoMember,
  type MultiRepoSyncOutcome,
} from "@/lib/mcpClient";
import { AddSubProjectPanel } from "./AddSubProjectPanel";

interface Props {
  projectId: string;
  /** Platform of the parent project, forwarded when adding a git sub-project. */
  platform: string;
  /** "git" or "zip"; decides which add path the panel offers. */
  sourceType: string;
  connectionKind?: string | null;
  azureDevOpsConnectionId?: string | null;
  githubAccountLogin?: string | null;
  /** Called after membership changes so the parent can refetch the project. */
  onChanged?: () => void;
}

function shortSha(sha: string | null): string {
  return sha ? sha.slice(0, 7) : "not scanned yet";
}

function formatWhen(value: string | null): string {
  if (!value) return "never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "never" : date.toLocaleString();
}

/**
 * Sub-projects of a project, and the way to add one.
 *
 * Without this the sub-projects were invisible: a project spanning ten
 * repositories showed a blank where a single-source project shows its URL, with
 * no way to see per-sub-project ingest state, drop one, or pull one.
 *
 * It renders for single-source projects too, even though they have no members
 * yet. It used to return null below two members, which meant a project that
 * started as one source could never gain a second -- the only route was deleting
 * it and re-importing everything, paying to regenerate documentation that had not
 * changed.
 *
 * "Sync" here is the manual equivalent of the push webhook, which is not yet
 * wired for multi-repo projects. Syncing one repository touches only that
 * repository's working tree, so the scan that follows sees unchanged bytes for
 * its siblings and only that sub-project's documentation is regenerated.
 */
export function MultiRepoMembersPanel({
  projectId,
  platform,
  sourceType,
  connectionKind,
  azureDevOpsConnectionId,
  githubAccountLogin,
  onChanged,
}: Props) {
  const [members, setMembers] = useState<MultiRepoMember[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyIdentity, setBusyIdentity] = useState<string | null>(null);
  const [syncingAll, setSyncingAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<MultiRepoSyncOutcome[]>([]);
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addNotice, setAddNotice] = useState<string | null>(null);
  // Sub-projects added from a ZIP. They are NOT git members, so the members
  // endpoint cannot list them and a refetch alone would leave the operator
  // looking at an unchanged panel after a successful upload. Anything that does
  // turn up as a member is dropped from here.
  const [pendingSubprojects, setPendingSubprojects] = useState<string[]>([]);

  // Stable identity even while `members` is still null. Handed to the add form,
  // which derives a memo from it; a fresh `[]` on every render would re-run that
  // form's repository fetch on a loop.
  const memberList = useMemo(() => members ?? [], [members]);

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
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading sub-projects&hellip;
      </p>
    );
  }

  const count = memberList.length;
  const memberNames = new Set(memberList.map((member) => member.subproject_name));
  const unlisted = pendingSubprojects.filter((name) => !memberNames.has(name));

  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium">
            {count === 0
              ? "One source, not split into sub-projects"
              : `${count} ${count === 1 ? "sub-project" : "sub-projects"}`}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {count === 0 ? (
              <>
                Adding a sub-project keeps the documentation this project already
                has: the next run regenerates only what is new.
              </>
            ) : (
              <>
                Each sub-project has its own documentation set under{" "}
                <code className="font-mono">{projectId}/&lt;name&gt;/docs</code>, plus a
                shared cross-project view.
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {count > 0 && (
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
          )}
          <Button
            type="button"
            variant={count === 0 ? "secondary" : "ghost"}
            size="sm"
            disabled={syncingAll || busyIdentity !== null}
            onClick={() => {
              setAddOpen((current) => !current);
              setAddNotice(null);
            }}
          >
            <Plus className="h-3.5 w-3.5" /> Add sub-project
          </Button>
        </div>
      </div>

      {addOpen && (
        <AddSubProjectPanel
          projectId={projectId}
          platform={platform}
          sourceType={sourceType}
          connectionKind={connectionKind}
          azureDevOpsConnectionId={azureDevOpsConnectionId}
          githubAccountLogin={githubAccountLogin}
          members={memberList}
          onAdded={async (summary, addedNames) => {
            setAddOpen(false);
            setAddNotice(summary);
            setPendingSubprojects(addedNames);
            // Refetch rather than patching local state: the server names the
            // sub-project and may have migrated the original tree into one of
            // its own, so its answer is the only correct list.
            await load();
            onChanged?.();
          }}
          onCancel={() => setAddOpen(false)}
        />
      )}

      {addNotice && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-emerald-700">
          <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{addNotice}</span>
        </p>
      )}

      {count > 0 && (
        <ul className="mt-3 divide-y divide-border">
          {memberList.map((member) => {
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
      )}

      {unlisted.length > 0 && (
        <ul className="mt-2 divide-y divide-border">
          {unlisted.map((name) => (
            <li key={name} className="flex flex-wrap items-center gap-2 py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  Uploaded archive · not scanned yet
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}

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

      {count > 0 &&
        outcomes.length === 0 &&
        !syncingAll &&
        busyIdentity === null &&
        members != null && (
          <p className="mt-2 text-xs text-muted-foreground">
            Push-triggered updates are not wired for multi-repo projects yet; use Sync.
          </p>
        )}

      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}
