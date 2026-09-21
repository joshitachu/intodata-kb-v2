"use client";

import { useMemo, useState, type ReactNode } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  FileText,
  FolderOpen,
  Link2,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { guidanceErrorMessage } from "@/hooks/useGuidance";
import {
  describeWorkspaceOrigin,
  type ProjectWorkspaceResponse,
  type WorkspaceFile,
} from "@/types/guidance";
import {
  ALWAYS_LOADED_LABEL,
  DeliveryChip,
  ON_DEMAND_LABEL,
  deliveryDetail,
} from "./costLabels";

/**
 * The files the assistant actually has, shown as files -- and changed here too.
 *
 * This is the answer to "what does it know about this project": a set of files
 * at known places, some put in front of it before every question and some it
 * opens only when a question needs them. Showing the real files rather than
 * describing them in prose removes the one thing that could be wrong -- a
 * description of a tree drifts from the tree; the tree cannot.
 *
 * It is also the only place anything is edited. The tree on the left is the
 * navigation and the pane on the right is the file, so the controls that write
 * a file belong in that pane and nowhere else: pick `.nexus/org/RULES.md` and
 * you read what is in it and add to it in one place. The alternative -- a
 * viewer here and a stack of editor cards further down the page -- is what this
 * replaces, and it made a reader hold two mental copies of the same list.
 *
 * The distinction this view exists to make unmissable is which files are sent
 * every time. It is carried four ways -- the headline number, a colour, a left
 * border and worded badges -- so it survives a colour-blind reader, a greyscale
 * screenshot and a glance.
 */

interface WorkspaceTreeProps {
  workspace: ProjectWorkspaceResponse | undefined;
  isLoading: boolean;
  error: unknown;
  /**
   * Paths listed in the tree that do not exist yet.
   *
   * A level whose text renders empty is not written at all, so a project with
   * no rules has no `.nexus/project/RULES.md` -- and with editing living in
   * this pane, a file that is missing from the tree is a file nobody can ever
   * write the first line of. Those paths are listed anyway and marked as not
   * created, rather than silently absent. They are excluded from the cost
   * counts and from both cost filters, because a file that does not exist
   * costs nothing and is neither sent nor withheld.
   */
  pendingPaths?: ReadonlySet<string>;
  /** Rendered in the viewer header, for the room left in the selected file. */
  fileBudget?: (file: WorkspaceFile) => ReactNode;
  /**
   * Controls rendered beside the selected file, used by the panel to say what
   * changes that file.
   */
  fileActions?: (file: WorkspaceFile) => ReactNode;
  /** Rendered under the file's text: whatever writes this particular file. */
  fileEditor?: (file: WorkspaceFile) => ReactNode;
}

const NO_PENDING: ReadonlySet<string> = new Set();

type CostFilter = "all" | "always" | "on-demand";

const FILTERS: { id: CostFilter; label: string }[] = [
  { id: "all", label: "All files" },
  { id: "always", label: ALWAYS_LOADED_LABEL },
  { id: "on-demand", label: ON_DEMAND_LABEL },
];

/**
 * Where a file came from, said as a person would say it.
 *
 * `describeWorkspaceOrigin` names the internal layer ("Organization layer").
 * That is the mechanism; this is the answer to "why is this here". Unknown
 * origins and uploaded documents fall through to the shared helper, which
 * already renders them readably.
 */
const PLAIN_ORIGINS: Record<string, string> = {
  assembled: "Gathered from everything else in this list",
  // Not "gathered from" anything: this is the role the assistant is given, and
  // it is the only always-loaded file whose text is not a copy of a section of
  // AGENTS.md. Saying so is the difference between a user understanding what
  // has authority over their answers and reading the token `system_prompt`.
  system_prompt: "What the assistant is told it is, before your question",
  org_layer: "Your organization's rules",
  project_layer: "This project's rules",
  person_layer: "Your own answer preferences",
  thread: "This conversation",
  manifest: "An index of the material available",
};

function plainOrigin(origin: string): string {
  return PLAIN_ORIGINS[origin] ?? describeWorkspaceOrigin(origin);
}

/**
 * Why a file is not a second, independent set of instructions.
 *
 * Two files restate text that is already elsewhere in the list: the assembled
 * `AGENTS.md` is built from the rule files, and the manifest is the same list
 * of material `AGENTS.md` already carries. Presenting either as its own source
 * would double-count what a reader believes is in force.
 */
function restatementNote(file: WorkspaceFile): string | null {
  if (file.origin === "assembled") {
    return "This file only gathers together the rules listed below it. Reading it is reading them again - it is not extra instructions.";
  }
  if (file.origin === "manifest") {
    return "A short index of what is available. It exists to be looked at again later, not to add anything new.";
  }
  return null;
}

// -- tree shape --------------------------------------------------------------

interface TreeDirectory {
  /** Last path segment. Empty for the root. */
  name: string;
  /** Full path of the directory, used as a stable key and test handle. */
  path: string;
  directories: TreeDirectory[];
  files: WorkspaceFile[];
}

/**
 * Group flat paths into the folders they name.
 *
 * Exported so the nesting can be asserted directly. A flat list that merely
 * *looks* indented is the failure this replaces: it cannot show that
 * `skills/52-test/SKILL.md` sits two levels inside `.nexus/project`, which is
 * the fact a user is trying to read off the screen.
 */
export function buildWorkspaceTree(files: readonly WorkspaceFile[]): TreeDirectory {
  const root: TreeDirectory = { name: "", path: "", directories: [], files: [] };

  for (const file of files) {
    const segments = file.path.split("/");
    const fileName = segments.pop();
    if (fileName === undefined) continue;

    let cursor = root;
    for (const segment of segments) {
      const path = cursor.path ? `${cursor.path}/${segment}` : segment;
      let next = cursor.directories.find((dir) => dir.path === path);
      if (!next) {
        next = { name: segment, path, directories: [], files: [] };
        cursor.directories.push(next);
      }
      cursor = next;
    }
    cursor.files.push(file);
  }

  sortDirectory(root);
  return root;
}

function sortDirectory(directory: TreeDirectory): void {
  directory.directories.sort((a, b) => a.name.localeCompare(b.name));
  directory.files.sort((a, b) => a.path.localeCompare(b.path));
  directory.directories.forEach(sortDirectory);
}

function fileName(path: string): string {
  return path.split("/").pop() ?? path;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} kB`;
}

/**
 * A token count in a unit a reader already has a feel for.
 *
 * "441 tokens" is meaningless to a consultant; "roughly 330 words" is about a
 * page, and that is a judgement they can make. The ratio is the usual English
 * rule of thumb and the wording says "roughly" because it is an approximation.
 * The token figure itself is still the server's, shown unchanged beside it --
 * this only adds a second way to read it, it never replaces it.
 */
function approximateWords(tokens: number): number {
  return Math.round(tokens * 0.75);
}

// -- component ---------------------------------------------------------------

export function WorkspaceTree({
  workspace,
  isLoading,
  error,
  pendingPaths,
  fileBudget,
  fileActions,
  fileEditor,
}: WorkspaceTreeProps) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [filter, setFilter] = useState<CostFilter>("all");
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());

  const files = useMemo(() => workspace?.files ?? [], [workspace]);
  const pending = pendingPaths ?? NO_PENDING;

  const visibleFiles = useMemo(
    () =>
      files.filter((file) =>
        filter === "all"
          ? true
          : pending.has(file.path)
            ? false
            : filter === "always"
              ? file.always_loaded
              : !file.always_loaded,
      ),
    [files, filter, pending],
  );

  const tree = useMemo(() => buildWorkspaceTree(visibleFiles), [visibleFiles]);

  // Derived rather than synchronised with an effect: when the project changes,
  // the previously selected path simply stops matching and the fallback takes
  // over, with no window in which the viewer shows another project's file.
  const selected =
    files.find((file) => file.path === selectedPath) ??
    files.find((file) => file.path === "AGENTS.md") ??
    files[0];

  if (isLoading) {
    return (
      <p className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Collecting the files for this project...
      </p>
    );
  }

  if (error != null) {
    return (
      <p
        role="alert"
        className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-3 text-sm text-destructive"
      >
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
        {guidanceErrorMessage(error, "Could not load the files for this project")}
      </p>
    );
  }

  if (!workspace) {
    return (
      <p className="rounded-lg border border-dashed border-border bg-muted/30 px-3 py-3 text-sm text-muted-foreground">
        Nothing has been read yet.
      </p>
    );
  }

  const real = files.filter((file) => !pending.has(file.path));
  const alwaysLoaded = real.filter((file) => file.always_loaded);
  const onDemand = real.filter((file) => !file.always_loaded);

  return (
    <section
      aria-labelledby="workspace-tree-heading"
      data-testid="workspace-tree"
      className="rounded-lg border border-border bg-card"
    >
      <WorkspaceCostHeader
        alwaysLoadedTokens={workspace.always_loaded_tokens}
        alwaysLoadedCount={alwaysLoaded.length}
        onDemandCount={onDemand.length}
      />

      {files.length === 0 ? (
        <p className="px-3 py-4 text-sm text-muted-foreground">
          There is nothing here yet. Upload a file to this project and it will
          appear in this list.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-1 border-b border-border px-3 py-2">
            {FILTERS.map((option) => (
              <button
                key={option.id}
                type="button"
                onClick={() => setFilter(option.id)}
                aria-pressed={filter === option.id}
                className={cn(
                  "rounded-full px-2.5 py-1 text-[11px] transition-colors",
                  filter === option.id
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>

          <div className="grid gap-0 lg:grid-cols-2">
            <div className="max-h-96 overflow-auto border-b border-border p-2 lg:border-b-0 lg:border-r">
              {visibleFiles.length === 0 ? (
                <p className="px-2 py-3 text-xs text-muted-foreground">
                  No file here is{" "}
                  {filter === "always"
                    ? "sent with every question."
                    : "left until it is needed."}
                </p>
              ) : (
                <DirectoryNode
                  directory={tree}
                  depth={0}
                  selectedPath={selected?.path ?? null}
                  onSelect={setSelectedPath}
                  pending={pending}
                  collapsed={collapsed}
                  onToggle={(path) =>
                    setCollapsed((current) => {
                      const next = new Set(current);
                      if (next.has(path)) next.delete(path);
                      else next.add(path);
                      return next;
                    })
                  }
                />
              )}

              <DocsSymlinkRow mounted={workspace.docs_mounted} />
            </div>

            <FileViewer
              file={selected}
              isPending={selected !== undefined && pending.has(selected.path)}
              fileBudget={fileBudget}
              fileActions={fileActions}
              fileEditor={fileEditor}
            />
          </div>
        </>
      )}
    </section>
  );
}

// -- header ------------------------------------------------------------------

interface WorkspaceCostHeaderProps {
  alwaysLoadedTokens: number;
  alwaysLoadedCount: number;
  onDemandCount: number;
}

/**
 * What this project costs the assistant before it has answered anything.
 *
 * `always_loaded_tokens` is the server's own figure and is used verbatim. It
 * already accounts for the fact that the assembled file restates the rule
 * files, so re-deriving it here by adding file sizes up would double-count that
 * text and quote a cost nobody is paying.
 */
function WorkspaceCostHeader({
  alwaysLoadedTokens,
  alwaysLoadedCount,
  onDemandCount,
}: WorkspaceCostHeaderProps) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-3 py-3">
      <div className="min-w-0">
        <h3 id="workspace-tree-heading" className="flex items-center gap-2 text-sm font-semibold">
          <FolderOpen className="h-4 w-4 text-muted-foreground" />
          Files the assistant has for this project
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Click any file to read exactly what the assistant reads, and to change
          what is in it.
        </p>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
          <span
            className="inline-flex items-center gap-1.5 text-amber-800 dark:text-amber-300"
            title={deliveryDetail(true)}
          >
            <span className="h-2 w-2 rounded-full bg-amber-500" aria-hidden />
            {alwaysLoadedCount} sent with every question
          </span>
          <span
            className="inline-flex items-center gap-1.5 text-muted-foreground"
            title={deliveryDetail(false)}
          >
            <span className="h-2 w-2 rounded-full border border-muted-foreground/60" aria-hidden />
            {onDemandCount} opened only when needed
          </span>
        </p>
      </div>

      <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-1.5 text-right dark:border-amber-900/60 dark:bg-amber-950/30">
        <p className="text-lg font-semibold leading-tight text-amber-900 dark:text-amber-200">
          {alwaysLoadedTokens.toLocaleString()} tokens
        </p>
        <p className="text-[10px] leading-tight text-amber-800 dark:text-amber-300">
          sent with every question
        </p>
        <p className="text-[10px] leading-tight text-amber-800/80 dark:text-amber-300/80">
          roughly {approximateWords(alwaysLoadedTokens).toLocaleString()} words
        </p>
      </div>
    </div>
  );
}

// -- tree rows ---------------------------------------------------------------

interface DirectoryNodeProps {
  directory: TreeDirectory;
  depth: number;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  pending: ReadonlySet<string>;
  collapsed: ReadonlySet<string>;
  onToggle: (path: string) => void;
}

function DirectoryNode({
  directory,
  depth,
  selectedPath,
  onSelect,
  pending,
  collapsed,
  onToggle,
}: DirectoryNodeProps) {
  const isRoot = directory.path === "";
  const isCollapsed = !isRoot && collapsed.has(directory.path);

  const children = (
    <ul className="space-y-0.5">
      {directory.directories.map((child) => (
        <DirectoryNode
          key={child.path}
          directory={child}
          depth={isRoot ? depth : depth + 1}
          selectedPath={selectedPath}
          onSelect={onSelect}
          pending={pending}
          collapsed={collapsed}
          onToggle={onToggle}
        />
      ))}
      {directory.files.map((file) => (
        <FileRow
          key={file.path}
          file={file}
          depth={isRoot ? depth : depth + 1}
          isSelected={file.path === selectedPath}
          isPending={pending.has(file.path)}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );

  if (isRoot) return children;

  return (
    <li data-testid={`workspace-dir-${directory.path}`}>
      <button
        type="button"
        onClick={() => onToggle(directory.path)}
        aria-expanded={!isCollapsed}
        className="flex w-full items-center gap-1 rounded px-1 py-1 text-left text-xs font-medium text-foreground hover:bg-muted"
        style={{ paddingLeft: depth * 14 + 4 }}
      >
        {isCollapsed ? (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="truncate">{directory.name}/</span>
      </button>
      {!isCollapsed && children}
    </li>
  );
}

interface FileRowProps {
  file: WorkspaceFile;
  depth: number;
  isSelected: boolean;
  /** Listed so it can be written, but not written yet. */
  isPending: boolean;
  onSelect: (path: string) => void;
}

function FileRow({ file, depth, isSelected, isPending, onSelect }: FileRowProps) {
  const always = file.always_loaded && !isPending;

  return (
    <li
      data-testid={`workspace-file-${file.path}`}
      data-always-loaded={always ? "true" : "false"}
      data-pending={isPending ? "true" : undefined}
    >
      <button
        type="button"
        onClick={() => onSelect(file.path)}
        aria-current={isSelected ? "true" : undefined}
        className={cn(
          "flex w-full items-center gap-1.5 rounded border-l-2 py-1 pr-1 text-left text-xs transition-colors",
          always
            ? "border-amber-500 bg-amber-50/60 font-medium text-amber-950 hover:bg-amber-100/70 dark:bg-amber-950/20 dark:text-amber-100"
            : "border-transparent text-muted-foreground hover:bg-muted",
          isPending && "italic",
          isSelected && "ring-1 ring-inset ring-primary/40",
        )}
        style={{ paddingLeft: depth * 14 + 6 }}
      >
        <FileText
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            always ? "text-amber-600" : "text-muted-foreground/70",
          )}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate">{fileName(file.path)}</span>
        {isPending ? (
          <span className="shrink-0 rounded-full border border-border bg-muted/60 px-2 py-0.5 text-[11px] leading-tight text-muted-foreground">
            {NOT_WRITTEN_YET}
          </span>
        ) : (
          <DeliveryChip alwaysLoaded={file.always_loaded} short />
        )}
      </button>
    </li>
  );
}

/** Said the same way on the row and in the pane. */
const NOT_WRITTEN_YET = "Nothing in it yet";

/**
 * The link to the generated documentation.
 *
 * Reported from `docs_mounted` rather than from the file list because it is not
 * a file: it is a pointer into the generated docs, which the assistant walks
 * the same way it walks any folder. Saying nothing when it is absent would
 * leave "the assistant cannot see the docs" looking identical to "it can".
 */
function DocsSymlinkRow({ mounted }: { mounted: boolean }) {
  return (
    <div
      data-testid="workspace-docs-symlink"
      className="mt-1 flex items-center gap-1.5 rounded border-l-2 border-transparent py-1 pl-1.5 pr-1 text-xs text-muted-foreground"
    >
      <Link2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" aria-hidden />
      <span className="min-w-0 flex-1 truncate">
        {mounted
          ? "docs - the generated documentation for this project"
          : "docs - not available for this project"}
      </span>
      {mounted ? (
        <DeliveryChip alwaysLoaded={false} short />
      ) : (
        <span className="shrink-0 rounded-full border border-destructive/30 bg-destructive/10 px-2 py-0.5 text-[11px] leading-tight text-destructive">
          Not available
        </span>
      )}
    </div>
  );
}

// -- viewer ------------------------------------------------------------------

interface FileViewerProps {
  file: WorkspaceFile | undefined;
  isPending: boolean;
  fileBudget?: (file: WorkspaceFile) => ReactNode;
  fileActions?: (file: WorkspaceFile) => ReactNode;
  fileEditor?: (file: WorkspaceFile) => ReactNode;
}

/**
 * The selected file: its text exactly as the assistant gets it, then whatever
 * writes it.
 *
 * The text stays scrollable and height-capped without exception -- a mounted
 * file is capped at 32k characters, which would otherwise push the editor below
 * several screens of prose and undo the point of putting them together.
 *
 * The room left in this file is reported here rather than in a card elsewhere,
 * because the ceiling is a fact about the file being edited and a meter that is
 * not beside the box you are typing into gets read as decoration.
 */
function FileViewer({
  file,
  isPending,
  fileBudget,
  fileActions,
  fileEditor,
}: FileViewerProps) {
  if (!file) {
    return (
      <div className="p-3 text-xs text-muted-foreground">
        Pick a file on the left to read what the assistant reads, and to change
        what is in it.
      </div>
    );
  }

  const restatement = isPending ? null : restatementNote(file);

  return (
    <div className="flex min-w-0 flex-col">
      <div className="space-y-1.5 border-b border-border px-3 py-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <p className="flex min-w-0 flex-wrap items-center gap-2">
            <span
              className="min-w-0 break-words text-xs font-medium text-foreground"
              data-testid="workspace-viewer-origin"
            >
              {plainOrigin(file.origin)}
            </span>
            {isPending ? (
              <span className="shrink-0 rounded-full border border-border bg-muted/60 px-2 py-0.5 text-[11px] leading-tight text-muted-foreground">
                {NOT_WRITTEN_YET}
              </span>
            ) : (
              <DeliveryChip alwaysLoaded={file.always_loaded} />
            )}
          </p>
          {!isPending && fileBudget?.(file)}
        </div>
        {!isPending && (
          <p className="text-[11px] text-muted-foreground">
            {deliveryDetail(file.always_loaded)}
          </p>
        )}
        <p className="text-[11px] text-muted-foreground/80">
          <code className="break-all font-mono" data-testid="workspace-viewer-path">
            {file.path}
          </code>
          {!isPending && (
            <>
              {" · "}
              {formatBytes(file.bytes)}
            </>
          )}
        </p>

        {restatement && (
          <p className="rounded-md border border-border bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground">
            <strong className="font-medium text-foreground">
              Nothing new in this file.
            </strong>{" "}
            {restatement}
          </p>
        )}

        {fileActions?.(file)}
      </div>

      {isPending ? (
        <p className="px-3 py-3 text-xs text-muted-foreground">
          This file does not exist yet, because nothing has been put in it. Add
          the first entry below and it is written for you.
        </p>
      ) : file.content === null ? (
        <p className="px-3 py-3 text-xs text-muted-foreground">
          The text of this file was not sent to this page.
        </p>
      ) : file.content.trim() === "" ? (
        <p className="px-3 py-3 text-xs text-muted-foreground">This file is empty.</p>
      ) : (
        <pre
          data-testid="workspace-viewer-content"
          className="max-h-96 overflow-auto whitespace-pre-wrap break-words p-3 text-[11px] leading-relaxed"
        >
          {file.content}
        </pre>
      )}

      {fileEditor?.(file)}
    </div>
  );
}
