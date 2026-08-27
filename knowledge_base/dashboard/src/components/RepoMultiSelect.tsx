import { useId, useMemo, useState, type ReactNode } from "react";
import {
  filterRepos,
  groupRepos,
  selectableRepos,
  type PickableRepo,
} from "../lib/repoSelection";

interface Props {
  repos: PickableRepo[];
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  loading?: boolean;
  onRefresh?: () => void;
  /** What scope the listing covers, shown above the list. */
  scopeHint?: string;
  emptyHint?: string;
  /** Extra actions rendered beside Refresh. */
  actions?: ReactNode;
  /** Per-repo branch pin, keyed by repo key. Absent/empty = follow the default. */
  branchOverrides?: Record<string, string>;
  onBranchChange?: (repoKey: string, branch: string) => void;
}

/**
 * Multi-select repository picker for the dashboard's Add Repository dialog.
 *
 * Same behaviour and copy as the RAG frontend's
 * `app/settings/sources/RepoMultiSelect.tsx`, restyled for the dashboard's dark
 * surface. Selecting several repos produces one project whose members are all
 * of them: each gets its own doc set and its own generation budget, plus a
 * shared cross-repo topology layer.
 *
 * Unavailable repos stay VISIBLE with a reason rather than being filtered out --
 * a repo missing from the list reads as a permissions bug, whereas "already
 * connected" explains itself.
 */
export default function RepoMultiSelect({
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
    return <p className="text-sm text-slate-500">Loading repositories…</p>;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-300">Repository access</p>
          {scopeHint && <p className="mt-0.5 text-xs text-slate-500">{scopeHint}</p>}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {actions}
          {onRefresh && (
            <button
              type="button"
              className="px-2 py-1 text-xs font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 hover:border-white/[0.2] transition-all"
              onClick={onRefresh}
            >
              Refresh
            </button>
          )}
        </div>
      </div>

      {repos.length === 0 ? (
        emptyHint ? <p className="text-xs text-slate-500">{emptyHint}</p> : null
      ) : (
        <>
          <div className="flex items-center gap-2">
            <input
              type="text"
              aria-label="Filter repositories"
              className="input-dark"
              placeholder="Filter repositories…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              type="button"
              className="shrink-0 px-2 py-1 text-xs font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 hover:border-white/[0.2] disabled:opacity-40 transition-all"
              disabled={visibleSelectable.length === 0}
              onClick={() =>
                setMany(visibleSelectable.map((repo) => repo.key), !allVisibleSelected)
              }
            >
              {allVisibleSelected ? "Clear all" : `Select all (${visibleSelectable.length})`}
            </button>
          </div>

          <div
            role="group"
            aria-label="Repositories"
            className="max-h-56 space-y-3 overflow-y-auto rounded border border-white/[0.07] p-2"
          >
            {groups.length === 0 && (
              <p className="px-1 py-1 text-xs text-slate-500">
                No repository matches “{query}”.
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
                    <p className="truncate text-xs font-semibold uppercase tracking-wide text-slate-500">
                      {group}
                    </p>
                    <button
                      type="button"
                      className="shrink-0 text-xs text-accent-primary hover:underline disabled:opacity-40"
                      disabled={groupSelectable.length === 0}
                      onClick={() =>
                        setMany(groupSelectable.map((repo) => repo.key), !groupAllSelected)
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
                          className={`flex flex-wrap items-center gap-2 rounded px-1 py-0.5 text-sm ${
                            disabled ? "text-slate-500" : "text-slate-300 hover:bg-white/[0.03]"
                          }`}
                        >
                          <input
                            id={inputId}
                            type="checkbox"
                            className="h-3.5 w-3.5 shrink-0"
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
                              <span className="shrink-0 text-xs italic text-slate-500">
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
                              className="h-7 w-40 shrink-0 rounded border border-white/[0.1] bg-surface-bg px-2 text-xs text-slate-300"
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

          <p className="text-xs text-slate-500">
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
