"use client";

import { useId, useMemo, useState } from "react";
import { Check, Loader2, RotateCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  filterRepos,
  groupRepos,
  selectableRepos,
  type PickableRepo,
} from "./repoSelection";

interface Props {
  repos: PickableRepo[];
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  loading?: boolean;
  onRefresh?: () => void;
  /** Shown above the list, e.g. what scope the listing covers. */
  scopeHint?: string;
  emptyHint?: string;
  /** Extra actions rendered beside Refresh (e.g. "Manage access"). */
  actions?: React.ReactNode;
  /** Per-repo branch pin, keyed by repo key. Absent/empty = follow the default. */
  branchOverrides?: Record<string, string>;
  onBranchChange?: (repoKey: string, branch: string) => void;
}

/**
 * Multi-select repository picker, shared by the GitHub and Azure DevOps panels.
 *
 * Replaces the single `<select>` both panels used. Selecting several repos
 * produces one project whose members are all of them: each gets its own doc set
 * and its own generation budget, plus a shared cross-repo topology layer.
 *
 * Grouping is by GitHub owner / Azure DevOps project, with a per-group toggle,
 * because a real selection is usually "everything under Talend" rather than ten
 * individual clicks. Unavailable repos stay VISIBLE with a reason rather than
 * being filtered out -- a repo missing from the list looks like a permissions
 * bug, whereas "already connected" is self-explanatory.
 */
export function RepoMultiSelect({
  repos,
  selected,
  onSelectedChange,
  loading = false,
  onRefresh,
  scopeHint,
  emptyHint,
  actions,
  branchOverrides,
  onBranchChange,
}: Props) {
  const [query, setQuery] = useState("");
  // Checkbox and label are associated by id rather than by wrapping the input,
  // so the label's own click never re-enters the control's activation path.
  const idPrefix = useId();

  const groups = useMemo(() => groupRepos(filterRepos(repos, query)), [repos, query]);
  const visibleSelectable = useMemo(
    () => selectableRepos(filterRepos(repos, query)),
    [repos, query],
  );
  const allVisibleSelected =
    visibleSelectable.length > 0 &&
    visibleSelectable.every((repo) => selected.has(repo.key));

  function toggle(key: string) {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onSelectedChange(next);
  }

  function setMany(keys: string[], value: boolean) {
    const next = new Set(selected);
    for (const key of keys) {
      if (value) next.add(key);
      else next.delete(key);
    }
    onSelectedChange(next);
  }

  if (loading) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading repositories&hellip;
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-sm font-medium">Repository access</p>
          {scopeHint && (
            <p className="mt-0.5 text-xs text-muted-foreground">{scopeHint}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {actions}
          {onRefresh && (
            <Button variant="ghost" size="sm" isLoading={loading} onClick={onRefresh}>
              <RotateCw className="h-3.5 w-3.5" /> Refresh
            </Button>
          )}
        </div>
      </div>

      {repos.length === 0 ? (
        emptyHint ? (
          <p className="text-xs text-muted-foreground">{emptyHint}</p>
        ) : null
      ) : (
        <>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                type="text"
                aria-label="Filter repositories"
                placeholder="Filter repositories&hellip;"
                className="pl-8"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={visibleSelectable.length === 0}
              onClick={() =>
                setMany(
                  visibleSelectable.map((repo) => repo.key),
                  !allVisibleSelected,
                )
              }
            >
              {allVisibleSelected ? "Clear all" : `Select all (${visibleSelectable.length})`}
            </Button>
          </div>

          <div
            role="group"
            aria-label="Repositories"
            className="max-h-72 space-y-3 overflow-y-auto rounded-md border border-border p-2"
          >
            {groups.length === 0 && (
              <p className="px-1 py-2 text-xs text-muted-foreground">
                No repository matches &ldquo;{query}&rdquo;.
              </p>
            )}
            {groups.map(({ group, repos: groupRepoList }) => {
              const groupSelectable = selectableRepos(groupRepoList);
              const groupAllSelected =
                groupSelectable.length > 0 &&
                groupSelectable.every((repo) => selected.has(repo.key));
              return (
                <div key={group}>
                  <div className="flex items-center justify-between gap-2 px-1">
                    <p className="truncate text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      {group}
                    </p>
                    <button
                      type="button"
                      className="shrink-0 text-xs text-primary hover:underline disabled:opacity-40"
                      disabled={groupSelectable.length === 0}
                      onClick={() =>
                        setMany(
                          groupSelectable.map((repo) => repo.key),
                          !groupAllSelected,
                        )
                      }
                    >
                      {groupAllSelected ? "none" : `all ${groupSelectable.length}`}
                    </button>
                  </div>
                  <ul className="mt-1 space-y-0.5">
                    {groupRepoList.map((repo) => {
                      const disabled = repo.unavailableReason !== null;
                      const isSelected = selected.has(repo.key);
                      const inputId = `${idPrefix}-${repo.key}`;
                      return (
                        <li
                          key={repo.key}
                          className={`flex flex-wrap items-center gap-2 rounded px-1 py-1 text-sm ${
                            disabled ? "text-muted-foreground" : "hover:bg-muted/50"
                          }`}
                        >
                          <input
                            id={inputId}
                            type="checkbox"
                            className="h-3.5 w-3.5 shrink-0 rounded border-input"
                            checked={isSelected}
                            disabled={disabled}
                            onChange={() => toggle(repo.key)}
                          />
                          <label
                            htmlFor={inputId}
                            className={`flex min-w-0 flex-1 items-center gap-2 ${
                              disabled ? "cursor-not-allowed" : "cursor-pointer"
                            }`}
                          >
                            <span className="min-w-0 flex-1 truncate">{repo.name}</span>
                            {repo.unavailableReason && (
                              <span className="shrink-0 text-xs italic">
                                {repo.unavailableReason}
                              </span>
                            )}
                            {/* Informational only -- the repo stays selectable. */}
                            {!repo.unavailableReason && repo.note && (
                              <span className="shrink-0 text-xs italic text-muted-foreground">
                                {repo.note}
                              </span>
                            )}
                          </label>
                          {/* Branch pin, shown only once a repo is selected so the
                              list stays scannable. Empty = follow the provider's
                              default, whose name is the placeholder. */}
                          {isSelected && onBranchChange && (
                            <input
                              type="text"
                              aria-label={`Branch for ${repo.name}`}
                              className="h-7 w-40 shrink-0 rounded-md border border-input bg-background px-2 text-xs"
                              placeholder={repo.defaultBranch ?? "default branch"}
                              value={branchOverrides?.[repo.key] ?? ""}
                              onChange={(e) => onBranchChange(repo.key, e.target.value)}
                            />
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </div>

          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {selected.size > 0 && <Check className="h-3.5 w-3.5 text-primary" aria-hidden />}
            {selected.size === 0
              ? "No repository selected."
              : selected.size === 1
              ? "1 repository selected."
              : `${selected.size} repositories selected — each gets its own documentation set, plus a shared cross-repo view.`}
          </p>
        </>
      )}
    </div>
  );
}
