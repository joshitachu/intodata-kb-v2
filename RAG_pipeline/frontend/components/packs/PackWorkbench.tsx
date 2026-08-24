"use client";

/**
 * The Packs workbench -- the one pack surface.
 *
 * A pack changes what documentation generation looks for and what it writes.
 * There are two shelves and they mean different things:
 *
 *   My packs      yours alone. Nobody else can see them. Edit freely.
 *   Shared packs  branches of the pack repository, available to everyone.
 *
 * The path between them is a pull request, so "Publish" is honest about what it
 * does: it pushes a branch and opens a request. A reviewer decides.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  KIND_LABELS,
  Pack,
  PackBranch,
  PackError,
  PromptSlot,
  PublishResult,
  StarterInfo,
  PackArchitectSession,
  abandonPackArchitectSession,
  cancelPackArchitectTurn,
  createPackArchitectSession,
  deletePack,
  discoverSharedBranches,
  groupPacks,
  groupSlotsByPhase,
  getCurrentPackArchitectSession,
  getPackArchitectSession,
  listPackFiles,
  listPacks,
  listPromptSlots,
  packSubtitle,
  publishPack,
  pullSharedPack,
  readPackFile,
  readPromptSlot,
  registerSharedBranch,
  savePackArchitectSession,
  selectProjectPack,
  sendPackArchitectMessage,
  starterInfo,
  writePackFile,
} from "@/lib/packs";

type Mode = "browse" | "create" | "edit";
const PACK_ARCHITECT_SESSION_KEY = "nexus-pack-architect-session";

interface Props {
  /** When set, the workbench also offers "Use for this project". */
  projectId?: string | null;
  /** Pack currently selected for that project, so the state is visible. */
  selectedPackId?: number | null;
  onSelectedPackChange?: (packId: number | null) => void;
  heading?: string;
}

function errorMessage(error: unknown): string {
  if (error instanceof PackError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export function PackWorkbench({
  projectId = null,
  selectedPackId = null,
  onSelectedPackChange,
  heading = "Packs",
}: Props) {
  const [packs, setPacks] = useState<Pack[]>([]);
  const [starter, setStarter] = useState<StarterInfo | null>(null);
  const [mode, setMode] = useState<Mode>("browse");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<number | null>(selectedPackId);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPacks(await listPacks());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    starterInfo().then(setStarter).catch(() => setStarter(null));
  }, [refresh]);

  useEffect(() => setSelected(selectedPackId), [selectedPackId]);

  const { shared, mine } = useMemo(() => groupPacks(packs), [packs]);

  const use = async (pack: Pack) => {
    if (!projectId) return;
    setBusy(true);
    setError(null);
    try {
      await selectProjectPack(projectId, pack.id);
      setSelected(pack.id);
      onSelectedPackChange?.(pack.id);
      setNotice(`${pack.name} will be used for this project.`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const clearSelection = async () => {
    if (!projectId) return;
    setBusy(true);
    try {
      await selectProjectPack(projectId, null);
      setSelected(null);
      onSelectedPackChange?.(null);
      setNotice("Using the built-in document set.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const [editing, setEditing] = useState<Pack | null>(null);

  if (mode === "create") {
    return (
      <CreatePack
        starter={starter}
        onCancel={() => setMode("browse")}
        onCreated={async (pack) => {
          setMode("browse");
          setNotice(`Created ${pack.name}. It is private to you until you publish it.`);
          await refresh();
        }}
      />
    );
  }

  if (mode === "edit" && editing) {
    return (
      <EditPack
        pack={editing}
        onClose={async () => {
          setMode("browse");
          setEditing(null);
          await refresh();
        }}
      />
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold tracking-tight text-foreground">{heading}</h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            A pack changes what documentation generation looks for and what it
            writes. Your packs are private to you; shared packs come from the
            pack repository and are available to everyone.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setMode("create")}
          className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-elevation-xs transition-colors hover:bg-primary/90"
        >
          New pack
        </button>
      </header>

      {error && <Banner tone="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner tone="info" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {projectId && (
        <div className="rounded-xl border border-border bg-muted/30 px-4 py-3 text-sm">
          <span className="text-muted-foreground">This project generates with: </span>
          <span className="font-medium text-foreground">
            {selected
              ? (packs.find((p) => p.id === selected)?.name ?? `pack ${selected}`)
              : "the built-in document set"}
          </span>
          {selected !== null && (
            <button
              type="button"
              onClick={clearSelection}
              disabled={busy}
              className="ml-3 text-xs text-muted-foreground underline hover:text-foreground disabled:opacity-50"
            >
              use the built-in set instead
            </button>
          )}
        </div>
      )}

      {loading ? (
        <p className="px-1 py-8 text-center text-sm text-muted-foreground">Loading packs…</p>
      ) : (
        <>
          <Shelf
            title="My packs"
            blurb="Private to you. Edit freely; publish when it is good enough for everyone."
            packs={mine}
            emptyText="You have not made a pack yet."
            projectId={projectId}
            selectedPackId={selected}
            busy={busy}
            onUse={use}
            onEdit={(pack) => {
              setEditing(pack);
              setMode("edit");
            }}
            onDeleted={async (pack) => {
              setNotice(`Deleted ${pack.name}.`);
              await refresh();
            }}
            onPublished={(pack, result) =>
              setNotice(
                result.pull_request_url
                  ? `${pack.name} is proposed for everyone: ${result.pull_request_url}`
                  : (result.note ?? `Pushed branch ${result.branch}.`),
              )
            }
            onError={setError}
          />

          <Shelf
            title="Shared packs"
            blurb="Reviewed and merged into the pack repository. Available to everyone."
            packs={shared}
            emptyText="No shared packs registered yet."
            projectId={projectId}
            selectedPackId={selected}
            busy={busy}
            onUse={use}
            onError={setError}
            onRefresh={refresh}
          />

          <SharedBranches starter={starter} onRegistered={refresh} onError={setError} />
        </>
      )}
    </div>
  );
}

function Banner({
  tone,
  children,
  onDismiss,
}: {
  tone: "error" | "info";
  children: React.ReactNode;
  onDismiss: () => void;
}) {
  const styles =
    tone === "error"
      ? "border-destructive/30 bg-destructive/5 text-destructive"
      : "border-primary/30 bg-primary/5 text-foreground";
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`flex items-start justify-between gap-3 rounded-xl border px-4 py-3 text-sm ${styles}`}>
      <div className="whitespace-pre-wrap break-words">{children}</div>
      <button type="button" onClick={onDismiss} className="shrink-0 text-xs underline opacity-80 hover:opacity-100">
        dismiss
      </button>
    </div>
  );
}

function Shelf({
  title,
  blurb,
  packs,
  emptyText,
  projectId,
  selectedPackId,
  busy,
  onUse,
  onEdit,
  onDeleted,
  onPublished,
  onError,
  onRefresh,
}: {
  title: string;
  blurb: string;
  packs: Pack[];
  emptyText: string;
  projectId: string | null;
  selectedPackId: number | null;
  busy: boolean;
  onUse: (pack: Pack) => void;
  onEdit?: (pack: Pack) => void;
  onDeleted?: (pack: Pack) => void;
  onPublished?: (pack: Pack, result: PublishResult) => void;
  onError: (message: string) => void;
  onRefresh?: () => void;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{blurb}</p>
        </div>
        {packs.length > 0 && (
          <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
            {packs.length}
          </span>
        )}
      </div>
      {packs.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          {emptyText}
        </p>
      ) : (
        <ul className="space-y-2">
          {packs.map((pack) => (
            <PackRow
              key={pack.id}
              pack={pack}
              isSelected={pack.id === selectedPackId}
              canSelect={Boolean(projectId)}
              busy={busy}
              onUse={onUse}
              onEdit={onEdit}
              onDeleted={onDeleted}
              onPublished={onPublished}
              onError={onError}
              onRefresh={onRefresh}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function PackRow({
  pack,
  isSelected,
  canSelect,
  busy,
  onUse,
  onEdit,
  onDeleted,
  onPublished,
  onError,
  onRefresh,
}: {
  pack: Pack;
  isSelected: boolean;
  canSelect: boolean;
  busy: boolean;
  onUse: (pack: Pack) => void;
  onEdit?: (pack: Pack) => void;
  onDeleted?: (pack: Pack) => void;
  onPublished?: (pack: Pack, result: PublishResult) => void;
  onError: (message: string) => void;
  onRefresh?: () => void;
}) {
  const [working, setWorking] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const run = async (action: () => Promise<void>) => {
    setWorking(true);
    try {
      await action();
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setWorking(false);
    }
  };

  const disabled = busy || working;

  return (
    <li
      className={`rounded-xl border p-3.5 transition-colors ${
        isSelected ? "border-primary/50 bg-primary/[0.04] shadow-elevation-xs" : "border-border bg-card"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium">{pack.name}</span>
            {pack.personal ? (
              <span className="rounded border border-border bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                private
              </span>
            ) : (
              <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-400">
                shared
              </span>
            )}
            {isSelected && (
              <span className="rounded bg-primary px-1.5 py-0.5 text-[11px] font-medium text-primary-foreground">
                in use
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{packSubtitle(pack)}</p>
          {pack.description && (
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{pack.description}</p>
          )}
          {!pack.usable && pack.blocked_reason && (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
              Cannot be used: {pack.blocked_reason}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canSelect && pack.usable && !isSelected && (
            <button
              type="button"
              onClick={() => onUse(pack)}
              disabled={disabled}
              className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground shadow-elevation-xs transition-colors hover:bg-muted/60 disabled:pointer-events-none disabled:opacity-50"
            >
              Use for this project
            </button>
          )}
          {pack.mutable && onEdit && (
            <button
              type="button"
              onClick={() => onEdit(pack)}
              disabled={disabled}
              className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground shadow-elevation-xs transition-colors hover:bg-muted/60 disabled:pointer-events-none disabled:opacity-50"
            >
              Edit
            </button>
          )}
          {pack.personal && onPublished && (
            <button
              type="button"
              title="Push this pack as a branch and open a pull request so everyone can use it"
              onClick={() =>
                run(async () => onPublished(pack, await publishPack(pack.id)))
              }
              disabled={disabled}
              className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground shadow-elevation-xs transition-colors hover:bg-muted/60 disabled:pointer-events-none disabled:opacity-50"
            >
              {working ? "Publishing…" : "Publish"}
            </button>
          )}
          {!pack.personal && pack.repo_url && onRefresh && (
            <button
              type="button"
              onClick={() =>
                run(async () => {
                  await pullSharedPack(pack.id);
                  onRefresh();
                })
              }
              disabled={disabled}
              className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground shadow-elevation-xs transition-colors hover:bg-muted/60 disabled:pointer-events-none disabled:opacity-50"
            >
              Update
            </button>
          )}
          {pack.personal && onDeleted && (
            confirmDelete ? (
              <span className="flex items-center gap-1 text-xs">
                <button
                  type="button"
                  onClick={() =>
                    run(async () => {
                      await deletePack(pack.id);
                      onDeleted(pack);
                    })
                  }
                  disabled={disabled}
                  className="rounded-md bg-destructive px-2.5 py-1.5 text-destructive-foreground transition-colors hover:bg-destructive/90 disabled:opacity-50"
                >
                  Delete
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="px-1 underline"
                >
                  cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                disabled={disabled}
                className="rounded-md border border-border px-2.5 py-1.5 text-xs text-destructive transition-colors hover:border-destructive/40 hover:bg-destructive/10 disabled:opacity-50"
              >
                Delete
              </button>
            )
          )}
        </div>
      </div>
    </li>
  );
}

/**
 * Creating a pack. The AI author is the default because writing a manifest by
 * hand means knowing the vocabulary first; the starter is the fallback for
 * anyone who would rather begin from a commented example.
 */
function CreatePack({
  starter,
  initialSession = null,
  sourcePack = null,
  onCancel,
  onCreated,
}: {
  starter: StarterInfo | null;
  initialSession?: PackArchitectSession | null;
  sourcePack?: Pack | null;
  onCancel: () => void;
  onCreated: (pack: Pack) => void;
}) {
  const [name, setName] = useState(sourcePack?.name.replace(/[-_]/g, " ") ?? "");
  const [message, setMessage] = useState("");
  const [platform, setPlatform] = useState("");
  const [session, setSession] = useState<PackArchitectSession | null>(initialSession);
  const [recoverable, setRecoverable] = useState<PackArchitectSession | null>(null);
  const [checkingRecovery, setCheckingRecovery] = useState(true);
  const [accepting, setAccepting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [startingOver, setStartingOver] = useState(false);
  const [selectedFile, setSelectedFile] = useState("plugin.yaml");
  const [clock, setClock] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialSession) return;
    const saved = window.localStorage.getItem(PACK_ARCHITECT_SESSION_KEY);
    const request = saved
      ? getPackArchitectSession(saved)
      : getCurrentPackArchitectSession();
    request
      .then((value) => {
        if (value.status === "active") {
          // Recovery is deliberately opt-in. Clicking "New pack" must never
          // silently replace the clean authoring form with an old starter or
          // incomplete draft.
          setRecoverable(value);
        }
        else window.localStorage.removeItem(PACK_ARCHITECT_SESSION_KEY);
      })
      .catch(() => {
        if (saved) window.localStorage.removeItem(PACK_ARCHITECT_SESSION_KEY);
      })
      .finally(() => setCheckingRecovery(false));
  }, [initialSession]);

  const resumeDraft = () => {
    if (!recoverable) return;
    setSession(recoverable);
    window.localStorage.setItem(PACK_ARCHITECT_SESSION_KEY, recoverable.id);
    if (recoverable.name) setName(recoverable.name.replace(/[-_]/g, " "));
    setRecoverable(null);
  };

  useEffect(() => {
    if (!session || session.turn_status !== "running") return;
    const sessionId = session.id;
    let stopped = false;
    const poll = async () => {
      try {
        const current = await getPackArchitectSession(sessionId);
        if (stopped) return;
        setSession(current);
        if (!name.trim() && current.name) setName(current.name.replace(/[-_]/g, " "));
      } catch (err) {
        if (!stopped) setError(errorMessage(err));
      }
    };
    const timer = window.setInterval(() => void poll(), 1_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [session?.id, session?.turn_status, name]);

  useEffect(() => {
    if (session?.turn_status !== "running") return;
    const timer = window.setInterval(() => setClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [session?.turn_status]);

  useEffect(() => {
    if (!session) return;
    const paths = new Set(["plugin.yaml", ...Object.keys(session.files)]);
    if (!paths.has(selectedFile)) setSelectedFile("plugin.yaml");
  }, [session, selectedFile]);

  const send = async () => {
    setAccepting(true);
    setError(null);
    try {
      const active = session ?? await createPackArchitectSession(platform.trim() || undefined);
      if (!session) {
        setSession(active);
        window.localStorage.setItem(PACK_ARCHITECT_SESSION_KEY, active.id);
      }
      const accepted = await sendPackArchitectMessage(active.id, message, active.revision);
      setSession(accepted);
      window.localStorage.setItem(PACK_ARCHITECT_SESSION_KEY, accepted.id);
      setMessage("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setAccepting(false);
    }
  };

  const useStarter = async () => {
    setAccepting(true);
    setError(null);
    try {
      const created = await createPackArchitectSession(platform.trim() || undefined);
      setSession(created);
      window.localStorage.setItem(PACK_ARCHITECT_SESSION_KEY, created.id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setAccepting(false);
    }
  };

  const cancelTurn = async () => {
    if (!session) return;
    setCancelling(true);
    setError(null);
    try {
      setSession(await cancelPackArchitectTurn(session.id, session.revision));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setCancelling(false);
    }
  };

  const startOver = async () => {
    if (!session) return;
    setStartingOver(true);
    setError(null);
    try {
      await abandonPackArchitectSession(session.id, session.revision);
      window.localStorage.removeItem(PACK_ARCHITECT_SESSION_KEY);
      if (sourcePack) {
        onCancel();
        return;
      }
      setSession(null);
      setName("");
      setMessage("");
      setSelectedFile("plugin.yaml");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setStartingOver(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const pack = await savePackArchitectSession(session!.id, name.trim(), session!.revision);
      window.localStorage.removeItem(PACK_ARCHITECT_SESSION_KEY);
      onCreated(pack);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const working = session?.turn_status === "running";
  const elapsedSeconds = session?.turn_started_at
    ? Math.max(0, Math.floor((clock - Date.parse(session.turn_started_at)) / 1_000))
    : 0;
  const elapsed = elapsedSeconds >= 60
    ? `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s`
    : `${elapsedSeconds}s`;
  const filePaths = session
    ? ["plugin.yaml", ...Object.keys(session.files).sort()]
    : [];
  const selectedContent = !session
    ? ""
    : selectedFile === "plugin.yaml"
      ? session.manifest_yaml
      : (session.files[selectedFile] ?? "");
  const stageLabel: Record<string, string> = {
    queued: "Turn saved",
    preparing: "Reading the current design",
    authoring: "Designing the Pack contract",
    applying: "Rendering canonical files",
    validating: "Checking the finished Pack",
    complete: "Turn complete",
    failed: "Turn stopped",
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
        <h2 className="text-lg font-semibold">{sourcePack ? `Refine ${sourcePack.name}` : session ? "Pack Architect" : "New pack"}</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          {session
            ? sourcePack
              ? "Ask the Pack Architect to refine this saved private Pack. Inspect the proposed files, then save the changes back to the same Pack."
              : "Refine this private draft through conversation, inspect its files, then save it when validation passes."
            : "Describe the Pack you need. A dedicated Pack Architect designs it; Nexus renders and validates all YAML and Markdown files."}
        </p>
        </div>
        {session && (
          <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${working ? "bg-blue-100 text-blue-800" : session.complete ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"}`}>
            {working ? "Authoring" : session.complete ? "Ready to save" : "Draft"}
          </span>
        )}
      </header>

      {error && <Banner tone="error" onDismiss={() => setError(null)}>{error}</Banner>}

      {!session ? <div className="max-w-3xl space-y-5 rounded-xl border border-border bg-card p-5">
      <label className="block max-w-sm">
        <span className="text-sm font-medium">Target platform</span>
        <span className="ml-2 text-xs text-muted-foreground">optional</span>
        <input
          value={platform}
          onChange={(e) => setPlatform(e.target.value)}
          placeholder="talend"
          pattern="[A-Za-z0-9_-]+"
          className="mt-1 w-full rounded-md border border-border bg-background p-2 text-sm text-foreground shadow-elevation-xs transition-[border-color,box-shadow] placeholder:text-muted-foreground/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
        <span className="mt-1 block text-xs text-muted-foreground">
          Adds lower-authority platform guidance. Nexus analysis policy always remains active.
        </span>
      </label>
      <label className="block">
        <span className="text-sm font-medium">What should this Pack do?</span>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={4}
          placeholder="Find every place a SQL query is built by concatenating a variable, trace where that value enters the job, and produce the documents needed to explain and remediate every finding."
          className="mt-1 w-full rounded-md border border-border bg-background p-2 text-sm text-foreground shadow-elevation-xs transition-[border-color,box-shadow] placeholder:text-muted-foreground/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={send}
          disabled={accepting || message.trim().length < 3}
          className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-elevation-xs transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
        >
          {accepting ? "Starting…" : "Start creating"}
        </button>
        {starter && (
          <button
            type="button"
            onClick={useStarter}
            disabled={accepting}
            className="text-xs text-muted-foreground underline hover:text-foreground"
            title={`Start from the commented starter (${starter.repo_url})`}
          >
            Use starter files as the draft
          </button>
        )}
      </div>
      {checkingRecovery ? (
        <p className="text-xs text-muted-foreground">Checking for an unfinished draft…</p>
      ) : recoverable ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-amber-950">
          <div>
            <p className="text-xs font-semibold">An unfinished draft is available</p>
            <p className="mt-0.5 text-xs text-amber-800">
              {recoverable.name?.replace(/[-_]/g, " ") || "Untitled Pack"} · {recoverable.files ? Object.keys(recoverable.files).length + 1 : 1} files
            </p>
          </div>
          <button
            type="button"
            onClick={resumeDraft}
            className="rounded-md border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium hover:bg-amber-100"
          >
            Resume previous draft
          </button>
        </div>
      ) : null}
      </div> : (
        <>
        {working && (
          <div role="status" className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-blue-950">
            <div className="flex items-start justify-between gap-4">
              <div className="flex min-w-0 gap-3">
                <span className="mt-0.5 h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-blue-600 border-t-transparent" />
                <div className="min-w-0">
                  <p className="text-sm font-semibold">{stageLabel[session.turn_stage ?? "queued"] ?? "Working"} · {elapsed}</p>
                  <p className="mt-1 text-xs leading-relaxed text-blue-800">{session.turn_detail}</p>
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-blue-700">
                    <span>✓ Turn saved</span>
                    <span>{session.turn_stage === "queued" || session.turn_stage === "preparing" ? "○" : "✓"} Design returned</span>
                    <span>{session.turn_stage === "validating" ? "✓" : "○"} Files rendered</span>
                    <span>{session.turn_stage === "validating" ? "●" : "○"} Validation</span>
                  </div>
                  <p className="mt-1 text-[11px] text-blue-700">One compact {session.model_id} request · no OpenCode loop or hidden repair calls</p>
                </div>
              </div>
              <button type="button" onClick={cancelTurn} disabled={cancelling} className="shrink-0 rounded-md border border-blue-300 bg-white px-2.5 py-1.5 text-xs font-medium hover:bg-blue-100 disabled:opacity-50">
                {cancelling ? "Cancelling…" : "Cancel turn"}
              </button>
            </div>
          </div>
        )}

        {session.turn_status === "failed" && session.turn_error && (
          <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            <p className="font-semibold">The turn stopped; your draft is safe.</p>
            <p className="mt-1">{session.turn_error}</p>
          </div>
        )}

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.25fr)_minmax(300px,0.75fr)]">
          <section className="flex min-h-[34rem] flex-col rounded-xl border border-border bg-card">
            <div className="border-b border-border px-4 py-3">
              <h3 className="text-sm font-semibold">Conversation</h3>
              <p className="mt-0.5 text-xs text-muted-foreground">Ask for additions, removals, or clarification. Each reply updates the complete design and Nexus rerenders its files.</p>
            </div>
            <div className="max-h-[32rem] flex-1 space-y-3 overflow-y-auto p-4">
              {session.messages.length === 0 && <p className="py-12 text-center text-sm text-muted-foreground">The starter is ready. Ask the Architect what to change.</p>}
              {session.messages.map((item, index) => (
                <div key={`${item.role}-${index}`} className={`max-w-[92%] rounded-lg px-3 py-2 text-sm ${item.role === "user" ? "ml-auto bg-primary text-primary-foreground" : "bg-muted text-foreground"}`}>
                  <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide opacity-70">{item.role === "user" ? "You" : "Pack Architect"}</p>
                  <p className="whitespace-pre-wrap leading-relaxed">{item.content}</p>
                </div>
              ))}
            </div>
            <div className="space-y-2 border-t border-border p-3">
              <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={3} disabled={working} placeholder="Add a remediation matrix and require exact source evidence for every finding." className="w-full rounded-md border border-border bg-background p-2 text-sm placeholder:text-muted-foreground/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:bg-muted" />
              <div className="flex items-center justify-between gap-2">
                <button type="button" onClick={startOver} disabled={startingOver || saving} className="text-xs text-muted-foreground underline hover:text-foreground disabled:opacity-50">{startingOver ? "Discarding…" : sourcePack ? "Discard AI draft" : "Start over"}</button>
                <button type="button" onClick={send} disabled={working || accepting || message.trim().length < 3} className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50">{accepting ? "Sending…" : "Send change"}</button>
              </div>
            </div>
          </section>

          <aside className="space-y-4">
            <section className="rounded-xl border border-border bg-card p-4">
              <label className="block">
                <span className="text-sm font-medium">Pack name</span>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder={session.name ?? "SQL injection review"} className="mt-1 w-full rounded-md border border-border bg-background p-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20" />
              </label>
              <div className={`mt-3 rounded-md border p-3 text-xs ${session.complete ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
                <p className="font-semibold">{working ? "Validation waits for this turn" : session.complete ? "Valid and ready to save" : "Draft needs more work"}</p>
                {!working && !session.complete && session.validation_errors.length > 0 && (
                  <ul className="mt-2 list-disc space-y-1 pl-4">{session.validation_errors.slice(0, 8).map((problem) => <li key={problem}>{problem}</li>)}</ul>
                )}
              </div>
            </section>

            <section className="overflow-hidden rounded-xl border border-border bg-card">
              <div className="border-b border-border px-4 py-3">
                <h3 className="text-sm font-semibold">Current Pack · {filePaths.length} files</h3>
              </div>
              <div className="grid max-h-[34rem] grid-rows-[auto_minmax(12rem,1fr)]">
                <div className="max-h-48 overflow-y-auto border-b border-border p-2">
                  {filePaths.map((path) => <button key={path} type="button" onClick={() => setSelectedFile(path)} className={`block w-full truncate rounded px-2 py-1.5 text-left font-mono text-xs ${selectedFile === path ? "bg-primary/10 font-semibold text-primary" : "hover:bg-muted"}`} title={path}>{path}</button>)}
                </div>
                <pre className="overflow-auto whitespace-pre-wrap break-words bg-muted/30 p-3 font-mono text-[11px] leading-relaxed text-foreground">{selectedContent || "This file is currently empty."}</pre>
              </div>
            </section>
          </aside>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <p className="text-xs text-muted-foreground">Private to your account until you publish it.</p>
          <div className="flex gap-2">
            <button type="button" onClick={onCancel} className="rounded-md border border-border bg-background px-3 py-2 text-sm font-medium hover:bg-muted/60">Close</button>
            <button type="button" onClick={save} disabled={working || saving || name.trim().length < 3 || !session.complete} className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50">{saving ? "Saving…" : session.complete ? sourcePack ? "Save changes" : "Save Pack" : "Fix validation issues first"}</button>
          </div>
        </div>
        </>
      )}
    </div>
  );
}

/**
 * Pick a prompt to override, grouped by the phase that sends it.
 *
 * Grouped by phase rather than listed alphabetically because the question an
 * author actually has is "what does Phase 3 do", not "what files exist". The
 * flat list was how `prompts/phase2.md` and `prompts/templates/phase2_analysis.md`
 * ended up looking interchangeable.
 */
function AddOverride({
  existing,
  onPick,
  onCancel,
}: {
  existing: string[];
  onPick: (slot: PromptSlot) => void;
  onCancel: () => void;
}) {
  const [slots, setSlots] = useState<PromptSlot[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listPromptSlots()
      .then(setSlots)
      .catch((err) => setError(errorMessage(err)));
  }, []);

  const have = new Set(existing);
  const groups = slots ? groupSlotsByPhase(slots) : [];

  return (
    <div className="space-y-3 rounded-xl border border-border bg-muted/30 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Add a prompt override</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Every prompt the pipeline sends. The file starts from the current
            built-in text, because an override replaces it outright — anything
            left out is gone.
          </p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-border bg-card px-2.5 py-1.5 text-xs hover:bg-muted"
        >
          Cancel
        </button>
      </div>

      {error && <Banner tone="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {!slots && !error && <p className="text-xs text-muted-foreground">Loading…</p>}

      <div className="max-h-96 space-y-3 overflow-y-auto">
        {groups.map((group) => (
          <section key={group.phase}>
            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {group.label}
            </h4>
            <ul className="mt-1 space-y-1">
              {group.slots.map((slot) => {
                const added = have.has(slot.path);
                return (
                  <li key={slot.path}>
                    <button
                      type="button"
                      disabled={added}
                      onClick={() => onPick(slot)}
                      className={`w-full rounded border px-2 py-1.5 text-left ${
                        added
                          ? "cursor-default border-border bg-muted opacity-60"
                          : "border-border bg-card hover:border-border/80"
                      }`}
                    >
                      <span className="block font-mono text-xs">{slot.path}</span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {slot.description}
                      </span>
                      <span className="mt-0.5 block text-[11px] text-muted-foreground">
                        {KIND_LABELS[slot.kind]}
                        {slot.has_default
                          ? ` · ${slot.default_bytes.toLocaleString()} bytes to start from`
                          : " · starts empty"}
                        {added ? " · already in this pack" : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

/** Editing a pack you own: every file in the tree, saved one at a time. */
function EditPack({ pack, onClose }: { pack: Pack; onClose: () => void }) {
  const [files, setFiles] = useState<string[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [adding, setAdding] = useState(false);
  const [startingAi, setStartingAi] = useState(false);
  const [aiSession, setAiSession] = useState<PackArchitectSession | null>(null);
  /** Path that exists only in the browser until saved, so the tree shows it. */
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    listPackFiles(pack.id)
      .then((paths) => {
        setFiles(paths);
        setActive(paths.includes("plugin.yaml") ? "plugin.yaml" : (paths[0] ?? null));
      })
      .catch((err) => setError(errorMessage(err)));
  }, [pack.id]);

  useEffect(() => {
    // A pending override's content is already in state and does not exist on
    // the server yet; fetching it would 404 and blank the editor.
    if (!active || active === pending) return;
    readPackFile(pack.id, active)
      .then((file) => {
        setContent(file.content);
        setDirty(false);
      })
      .catch((err) => setError(errorMessage(err)));
  }, [pack.id, active, pending]);

  const startOverride = async (slot: PromptSlot) => {
    setError(null);
    setNotice(null);
    try {
      const seed = slot.has_default ? await readPromptSlot(slot.path) : { content: "" };
      setFiles((current) =>
        current.includes(slot.path) ? current : [...current, slot.path].sort(),
      );
      setPending(slot.path);
      setActive(slot.path);
      setContent(seed.content);
      setDirty(true);
      setAdding(false);
      setNotice(
        slot.has_default
          ? `${slot.path} started from the built-in text. Nothing changes until you save.`
          : `${slot.path} is empty — whatever you write here is added to the prompt.`,
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const save = async () => {
    if (!active) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      await writePackFile(pack.id, active, content);
      setDirty(false);
      if (pending === active) setPending(null);
      setNotice(`Saved ${active}.`);
    } catch (err) {
      // A rejected plugin.yaml is the common case and the message names the
      // exact field, so it must be shown verbatim rather than summarised.
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const startAiRefinement = async () => {
    if (dirty) {
      setError("Save or discard the current manual file edit before refining with AI.");
      return;
    }
    setStartingAi(true);
    setError(null);
    try {
      const created = await createPackArchitectSession(undefined, pack.id);
      window.localStorage.setItem(PACK_ARCHITECT_SESSION_KEY, created.id);
      setAiSession(created);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setStartingAi(false);
    }
  };

  if (aiSession) {
    return (
      <CreatePack
        starter={null}
        initialSession={aiSession}
        sourcePack={pack}
        onCancel={() => setAiSession(null)}
        onCreated={() => onClose()}
      />
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{pack.name}</h2>
          <p className="text-xs text-muted-foreground">{packSubtitle(pack)}</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={startAiRefinement}
            disabled={startingAi}
            className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-elevation-xs transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {startingAi ? "Starting AI…" : "Refine with AI"}
          </button>
          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm font-medium text-foreground shadow-elevation-xs transition-colors hover:bg-muted/60"
          >
            Add prompt override
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm font-medium text-foreground shadow-elevation-xs transition-colors hover:bg-muted/60"
          >
            Back to packs
          </button>
        </div>
      </header>

      {error && <Banner tone="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner tone="info" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {adding && (
        <AddOverride
          existing={files}
          onPick={startOverride}
          onCancel={() => setAdding(false)}
        />
      )}

      <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
        <ul className="space-y-1">
          {files.map((path) => (
            <li key={path}>
              <button
                type="button"
                onClick={() => setActive(path)}
                className={`w-full truncate rounded px-2 py-1 text-left font-mono text-xs ${
                  path === active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                {path}
                {path === pending && (
                  <span className="ml-1 font-sans text-[10px] uppercase opacity-70">
                    unsaved
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>

        <div className="space-y-2">
          <textarea
            value={content}
            onChange={(e) => {
              setContent(e.target.value);
              setDirty(true);
            }}
            rows={26}
            spellCheck={false}
            className="w-full rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed text-foreground shadow-elevation-xs transition-[border-color,box-shadow] focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
          <button
            type="button"
            onClick={save}
            disabled={saving || !dirty}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-elevation-xs transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
          >
            {saving ? "Saving…" : dirty ? "Save" : "Saved"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Branches of the shared repository that are valid packs but not registered. */
function SharedBranches({
  starter,
  onRegistered,
  onError,
}: {
  starter: StarterInfo | null;
  onRegistered: () => void;
  onError: (message: string) => void;
}) {
  const [branches, setBranches] = useState<PackBranch[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const result = await discoverSharedBranches();
      setBranches(result.branches);
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const unregistered = (branches ?? []).filter((b) => !b.registered);

  return (
    <section className="space-y-3 border-t border-border pt-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-foreground">Pack repository</h3>
          <p className="text-xs text-muted-foreground">
            {starter?.repo_url ?? "Every branch that is a valid pack."}
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground shadow-elevation-xs transition-colors hover:bg-muted/60 disabled:pointer-events-none disabled:opacity-50"
        >
          {loading ? "Checking…" : "Check for packs"}
        </button>
      </div>

      {branches !== null && unregistered.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Every pack in the repository is already registered.
        </p>
      )}

      {unregistered.length > 0 && (
        <ul className="space-y-2">
          {unregistered.map((branch) => (
            <li
              key={branch.branch}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-3.5"
            >
              <div className="min-w-0">
                <span className="font-mono text-sm">{branch.branch}</span>
                <p className="text-xs text-muted-foreground">
                  {branch.manifest_name} v{branch.version} · {branch.commit_sha.slice(0, 7)}
                </p>
                {branch.description && (
                  <p className="mt-1 text-sm text-muted-foreground">{branch.description}</p>
                )}
              </div>
              <button
                type="button"
                onClick={async () => {
                  setWorking(branch.branch);
                  try {
                    await registerSharedBranch(branch.repo_url, branch.branch);
                    await load();
                    onRegistered();
                  } catch (err) {
                    onError(errorMessage(err));
                  } finally {
                    setWorking(null);
                  }
                }}
                disabled={working === branch.branch}
                className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground shadow-elevation-xs transition-colors hover:bg-muted/60 disabled:pointer-events-none disabled:opacity-50"
              >
                {working === branch.branch ? "Adding…" : "Add"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
