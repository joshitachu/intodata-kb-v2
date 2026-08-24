"use client";

import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Database,
  KeyRound,
  MapPinned,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Server,
  Trash2,
} from "lucide-react";
import { useUserProjects } from "@/hooks/useUserProjects";
import { apiErrorMessage } from "@/lib/apiError";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { SelectMenu } from "@/components/ui/select-menu";
import {
  Empty,
  EntityTable,
  Kpi,
  Panel,
  RelativeTime,
  formatCompact,
  formatInterval,
} from "@/components/runtime/runtime-ui";
import { cn } from "@/lib/utils";

type CredentialField = {
  name: string;
  type: "secret" | "url" | "text" | string;
  label?: string | null;
  required?: boolean;
};

type SourceType = {
  provider_key: string;
  provider_label: string;
  credential_schema: CredentialField[];
};

type SourceHealth = {
  last_attempt_at?: string | null;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  consecutive_failures?: number;
  last_failure_sanitized?: string | null;
  total_cycles?: number;
  total_records_ingested?: number;
};

type RuntimeSource = {
  id: number;
  project_id: string;
  source_key: string;
  source_type: string;
  label: string;
  status: string;
  enabled: boolean;
  sync_interval_seconds: number;
  disabled_reason?: string | null;
  last_synced_at?: string | null;
  health?: SourceHealth | null;
};

type WorkspaceMap = {
  id: number;
  source_id: number;
  container_key: string;
  container_name?: string | null;
  project_id: string;
  created_at: string;
};

type AuditEvent = {
  id: number;
  action: string;
  username?: string | null;
  details?: Record<string, unknown> | null;
  created_at?: string | null;
};

type SourceStatus = "enabled" | "disabled" | "auto-disabled" | "error";

const STATUS_FILTERS: Array<{ value: "all" | SourceStatus; label: string }> = [
  { value: "all", label: "All" },
  { value: "enabled", label: "Enabled" },
  { value: "disabled", label: "Disabled" },
  { value: "auto-disabled", label: "Auto-disabled" },
  { value: "error", label: "Error" },
];

function sourceStatus(source: RuntimeSource): SourceStatus {
  if (!source.enabled && source.disabled_reason === "auto-disabled") return "auto-disabled";
  if (!source.enabled) return "disabled";
  if (source.status === "failed") return "error";
  return "enabled";
}

/**
 * Read an error out of a failed response.
 *
 * The previous implementation assigned `body.detail` straight into React state.
 * FastAPI returns a structured `detail` for validation errors, and rendering an
 * object as a child throws — so a bad request crashed the page instead of
 * reporting itself. `apiErrorMessage` always yields a string.
 */
async function failureMessage(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  const named = (body as { error?: unknown } | null)?.error;
  if (typeof named === "string" && named.trim()) return named;
  return apiErrorMessage((body as { detail?: unknown } | null)?.detail, fallback);
}

export default function AdminRuntimePage() {
  const projects = useUserProjects();
  // Stable identity: `?? []` inside JSX would hand every child a new array on
  // each render and defeat their dependency arrays.
  const projectOptions = useMemo(() => projects.data?.projects ?? [], [projects.data]);
  const [sources, setSources] = useState<RuntimeSource[]>([]);
  const [sourceTypes, setSourceTypes] = useState<SourceType[]>([]);
  const [selected, setSelected] = useState<RuntimeSource | null>(null);
  const [statusFilter, setStatusFilter] = useState<"all" | SourceStatus>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [rotateFor, setRotateFor] = useState<RuntimeSource | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<RuntimeSource | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sourcesRes, typesRes] = await Promise.all([
        fetch("/api/runtime/sources", { cache: "no-store" }),
        fetch("/api/runtime/source-types", { cache: "no-store" }),
      ]);
      if (!sourcesRes.ok) throw new Error(await failureMessage(sourcesRes, "Failed to load runtime sources"));
      if (!typesRes.ok) throw new Error(await failureMessage(typesRes, "Failed to load source types"));
      const sourcesJson = await sourcesRes.json();
      const typesJson = await typesRes.json();
      const nextSources: RuntimeSource[] = sourcesJson.sources ?? [];
      setSources(nextSources);
      setSourceTypes(typesJson.source_types ?? []);
      setSelected((current) =>
        current ? nextSources.find((source) => source.id === current.id) ?? null : null,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Runtime source request failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const filteredSources = useMemo(
    () =>
      statusFilter === "all"
        ? sources
        : sources.filter((source) => sourceStatus(source) === statusFilter),
    [sources, statusFilter],
  );

  const autoDisabled = sources.filter((source) => sourceStatus(source) === "auto-disabled");
  const enabledCount = sources.filter((source) => source.enabled).length;
  const failing = sources.filter((source) => (source.health?.consecutive_failures ?? 0) > 0).length;
  const ingested = sources.reduce(
    (sum, source) => sum + (source.health?.total_records_ingested ?? 0),
    0,
  );

  /** Every write funnels through here so no failure can pass silently. */
  async function run(sourceId: number, work: () => Promise<void>) {
    setBusyId(sourceId);
    setError(null);
    try {
      await work();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusyId(null);
    }
  }

  function patchSource(source: RuntimeSource, patch: Partial<RuntimeSource>) {
    return run(source.id, async () => {
      const res = await fetch(`/api/runtime/sources/${source.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) throw new Error(await failureMessage(res, "Update failed"));
    });
  }

  function confirmDelete() {
    const target = deleteTarget;
    if (!target) return;
    setDeleteTarget(null);
    void run(target.id, async () => {
      const res = await fetch(`/api/runtime/sources/${target.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await failureMessage(res, "Delete failed"));
      setSelected(null);
    });
  }

  function syncNow(source: RuntimeSource) {
    return run(source.id, async () => {
      const res = await fetch(`/api/runtime/sources/${source.id}/sync`, { method: "POST" });
      if (!res.ok) throw new Error(await failureMessage(res, "Sync failed"));
    });
  }

  return (
    <main className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">Runtime sources</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Where imported runtime data comes from — polling schedule, credential rotation,
            container mapping and sync health.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button variant="secondary" size="md" onClick={() => void refresh()} isLoading={loading}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Refresh
          </Button>
          <Button size="md" onClick={() => setAddOpen(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden />
            Add source
          </Button>
        </div>
      </header>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span className="min-w-0 flex-1 break-words">{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="shrink-0 text-xs underline opacity-80 hover:opacity-100"
          >
            dismiss
          </button>
        </div>
      )}

      <section aria-label="Summary" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi icon={Database} label="Sources" value={String(sources.length)} hint="registered" />
        <Kpi
          icon={Play}
          label="Polling"
          value={String(enabledCount)}
          hint={`${sources.length - enabledCount} not polling`}
          tone={enabledCount > 0 ? "good" : "warning"}
        />
        <Kpi
          icon={AlertTriangle}
          label="Failing"
          value={String(failing)}
          hint={autoDisabled.length > 0 ? `${autoDisabled.length} auto-disabled` : "consecutive failures"}
          tone={failing > 0 ? "danger" : "good"}
        />
        <Kpi
          icon={ScrollText}
          label="Records ingested"
          value={formatCompact(ingested)}
          hint="all cycles"
        />
      </section>

      {autoDisabled.length > 0 && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-4">
          <p className="flex items-center gap-2 text-sm font-medium text-amber-800 dark:text-amber-400">
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
            Auto-disabled after repeated failures
          </p>
          <ul className="mt-3 space-y-2">
            {autoDisabled.map((source) => (
              <li
                key={source.id}
                className="flex flex-wrap items-center justify-between gap-2 text-sm"
              >
                <span className="min-w-0 text-amber-800 dark:text-amber-400">
                  <span className="font-medium">{source.label}</span>
                  {": "}
                  {source.health?.last_failure_sanitized ?? "sync failure"}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  isLoading={busyId === source.id}
                  onClick={() => void patchSource(source, { enabled: true })}
                >
                  <Play className="h-3.5 w-3.5" aria-hidden />
                  Re-enable
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <Panel
          title="Sources"
          icon={Server}
          description="Select a row to inspect sync health, container mappings and the audit trail."
          action={
            <div
              role="radiogroup"
              aria-label="Filter by status"
              className="flex flex-wrap gap-1 rounded-lg border border-border bg-muted/30 p-0.5"
            >
              {STATUS_FILTERS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="radio"
                  aria-checked={statusFilter === option.value}
                  onClick={() => setStatusFilter(option.value)}
                  className={cn(
                    "rounded-md px-2 py-1 text-xs font-medium transition-colors",
                    statusFilter === option.value
                      ? "bg-card text-foreground shadow-elevation-xs"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          }
          flush
        >
          <EntityTable
            headers={["Source", "Project", "Status", "Last success", "Every", "Actions"]}
            rows={filteredSources}
            rowKey={(source) => String(source.id)}
            onClick={(source) => setSelected(source)}
            emptyLabel={
              loading
                ? "Loading runtime sources..."
                : statusFilter === "all"
                  ? "No runtime sources are registered yet."
                  : `No sources are currently ${statusFilter}.`
            }
            cells={(source) => [
              <span key="label" className="block min-w-0">
                <span className="block truncate font-medium text-foreground">{source.label}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {source.source_type}
                </span>
              </span>,
              source.project_id,
              <StatusBadge key="status" status={sourceStatus(source)} />,
              <RelativeTime
                key="success"
                value={source.health?.last_success_at ?? source.last_synced_at}
              />,
              formatInterval(source.sync_interval_seconds),
              <span
                key="actions"
                className="flex flex-wrap gap-1"
                onClick={(event) => event.stopPropagation()}
              >
                <IconButton
                  label="Sync now"
                  onClick={() => void syncNow(source)}
                  disabled={!source.enabled || busyId === source.id}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                </IconButton>
                <IconButton label="Rotate credentials" onClick={() => setRotateFor(source)}>
                  <KeyRound className="h-3.5 w-3.5" />
                </IconButton>
                <IconButton
                  label={source.enabled ? "Disable" : "Enable"}
                  onClick={() => void patchSource(source, { enabled: !source.enabled })}
                  disabled={busyId === source.id}
                >
                  {source.enabled ? (
                    <Pause className="h-3.5 w-3.5" />
                  ) : (
                    <Play className="h-3.5 w-3.5" />
                  )}
                </IconButton>
                <IconButton
                  label="Delete"
                  tone="danger"
                  onClick={() => setDeleteTarget(source)}
                  disabled={busyId === source.id}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </IconButton>
              </span>,
            ]}
          />
        </Panel>

        <SourceDetail
          source={selected}
          projects={projectOptions}
          onRefresh={refresh}
          onError={setError}
        />
      </div>

      <SourceDialog
        open={addOpen}
        projects={projectOptions}
        sourceTypes={sourceTypes}
        onClose={() => setAddOpen(false)}
        onSaved={async () => {
          setAddOpen(false);
          await refresh();
        }}
      />

      <RotateDialog
        source={rotateFor}
        sourceType={
          sourceTypes.find((type) => type.provider_key === rotateFor?.source_type) ?? null
        }
        onClose={() => setRotateFor(null)}
        onSaved={async () => {
          setRotateFor(null);
          await refresh();
        }}
      />

      <Modal
        open={deleteTarget !== null}
        title="Delete runtime source"
        description={`${deleteTarget?.label ?? ""} and its stored credentials are removed. Imported runtime data already in the store is not deleted.`}
        onClose={() => setDeleteTarget(null)}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" size="sm" onClick={confirmDelete}>
              Delete source
            </Button>
          </>
        }
      >
        <span className="sr-only">Confirm deletion</span>
      </Modal>
    </main>
  );
}

const STATUS_STYLES: Record<SourceStatus, string> = {
  enabled: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  "auto-disabled": "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  error: "border-destructive/30 bg-destructive/10 text-destructive",
  disabled: "border-border bg-muted text-muted-foreground",
};

function StatusBadge({ status }: { status: SourceStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
        STATUS_STYLES[status],
      )}
    >
      {status}
    </span>
  );
}

function IconButton({
  label,
  onClick,
  disabled,
  tone = "default",
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "default" | "danger";
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "inline-flex h-7 w-7 items-center justify-center rounded-md border border-border transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-40",
        tone === "danger"
          ? "text-muted-foreground hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/** Text input for a provider credential field, typed from its schema entry. */
function CredentialInput({
  field,
  value,
  onChange,
}: {
  field: CredentialField;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block space-y-1.5 text-xs font-medium text-muted-foreground">
      {field.label ?? field.name}
      <Input
        type={field.type === "secret" ? "password" : field.type === "url" ? "url" : "text"}
        required={field.required !== false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
        placeholder={field.type === "secret" ? "Write-only; never returned" : undefined}
      />
    </label>
  );
}

function SourceDialog({
  open,
  projects,
  sourceTypes,
  onClose,
  onSaved,
}: {
  open: boolean;
  projects: { id: string; display_name: string }[];
  sourceTypes: SourceType[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [providerKey, setProviderKey] = useState("");
  const [projectId, setProjectId] = useState("");
  const [label, setLabel] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState(60);
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Defaults are applied when the dialog opens, not at first mount: the options
  // arrive from two async queries and were previously read once, while empty.
  useEffect(() => {
    if (!open) return;
    setProviderKey(sourceTypes[0]?.provider_key ?? "");
    setProjectId(projects[0]?.id ?? "");
    setError("");
  }, [open, sourceTypes, projects]);

  const selectedType = sourceTypes.find((type) => type.provider_key === providerKey);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/runtime/sources", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          provider_key: providerKey,
          label: label || selectedType?.provider_label || providerKey,
          sync_interval_seconds: intervalSeconds,
          credentials,
        }),
      });
      // Only clear the typed secrets once the write actually succeeded —
      // wiping them on failure forced a full retype of every credential.
      if (!res.ok) {
        setError(await failureMessage(res, "Could not create the source"));
        return;
      }
      setCredentials({});
      setLabel("");
      onSaved();
    } catch {
      setError("Network error while creating the source");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Add runtime source"
      description="Credentials are encrypted at rest and never returned to the browser."
      onClose={onClose}
      size="lg"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" form="add-source-form" type="submit" isLoading={busy}>
            Save source
          </Button>
        </>
      }
    >
      <form id="add-source-form" className="space-y-3" onSubmit={submit}>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground">Type</p>
            <SelectMenu
              value={providerKey}
              onChange={setProviderKey}
              options={sourceTypes.map((type) => ({
                value: type.provider_key,
                label: type.provider_label,
              }))}
              placeholder="Select a provider"
              ariaLabel="Source type"
              variant="field"
              size="md"
              className="w-full max-w-none"
            />
          </div>
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground">Project</p>
            <SelectMenu
              value={projectId}
              onChange={setProjectId}
              options={projects.map((project) => ({
                value: project.id,
                label: project.display_name,
              }))}
              placeholder="Select a project"
              ariaLabel="Project"
              variant="field"
              size="md"
              className="w-full max-w-none"
            />
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
            Name
            <Input
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder={selectedType?.provider_label ?? "Display name"}
            />
          </label>
          <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
            Poll interval (seconds)
            <Input
              type="number"
              min={30}
              value={intervalSeconds}
              onChange={(event) => setIntervalSeconds(Number(event.target.value))}
            />
          </label>
        </div>
        {selectedType?.credential_schema.map((field) => (
          <CredentialInput
            key={field.name}
            field={field}
            value={credentials[field.name] ?? ""}
            onChange={(value) => setCredentials((prev) => ({ ...prev, [field.name]: value }))}
          />
        ))}
        {error && (
          <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}

function RotateDialog({
  source,
  sourceType,
  onClose,
  onSaved,
}: {
  source: RuntimeSource | null;
  sourceType: SourceType | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (source) setError("");
  }, [source]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!source) return;
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`/api/runtime/sources/${source.id}/credentials`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ credentials }),
      });
      if (!res.ok) {
        setError(await failureMessage(res, "Could not rotate the credentials"));
        return;
      }
      setCredentials({});
      onSaved();
    } catch {
      setError("Network error while rotating credentials");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={source !== null}
      title={`Rotate credentials — ${source?.label ?? ""}`}
      description="The previous secret is replaced immediately. Test a sync afterwards."
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" form="rotate-form" type="submit" isLoading={busy}>
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            Rotate
          </Button>
        </>
      }
    >
      <form id="rotate-form" className="space-y-3" onSubmit={submit}>
        {(sourceType?.credential_schema ?? []).map((field) => (
          <CredentialInput
            key={field.name}
            field={field}
            value={credentials[field.name] ?? ""}
            onChange={(value) => setCredentials((prev) => ({ ...prev, [field.name]: value }))}
          />
        ))}
        {error && (
          <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}

function SourceDetail({
  source,
  projects,
  onRefresh,
  onError,
}: {
  source: RuntimeSource | null;
  projects: { id: string; display_name: string }[];
  onRefresh: () => void;
  onError: (message: string) => void;
}) {
  const [mappings, setMappings] = useState<WorkspaceMap[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [containerKey, setContainerKey] = useState("");
  const [containerName, setContainerName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [busy, setBusy] = useState(false);

  const sourceId = source?.id ?? null;

  const refreshMappings = useCallback(async () => {
    if (sourceId === null) return;
    const res = await fetch(`/api/runtime/sources/${sourceId}/workspace-map`, { cache: "no-store" });
    if (!res.ok) return;
    const body = await res.json();
    setMappings(body.mappings ?? []);
  }, [sourceId]);

  const refreshAudit = useCallback(async () => {
    if (sourceId === null) return;
    const res = await fetch(`/api/runtime/audit?source_id=${encodeURIComponent(sourceId)}`, {
      cache: "no-store",
    });
    if (!res.ok) return;
    const body = await res.json();
    setAuditEvents(body.events ?? []);
  }, [sourceId]);

  // Kept separate on purpose. Both callbacks key on `sourceId` only, so mapping
  // and audit reads fire when the selection changes — not on every render of
  // the parent, which would otherwise re-fetch in a loop.
  useEffect(() => {
    void refreshMappings();
    void refreshAudit();
  }, [refreshMappings, refreshAudit]);

  useEffect(() => {
    setProjectId((current) => current || projects[0]?.id || "");
  }, [projects]);

  async function addMapping(event: FormEvent) {
    event.preventDefault();
    if (!source) return;
    setBusy(true);
    try {
      const res = await fetch(`/api/runtime/sources/${source.id}/workspace-map`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          container_key: containerKey,
          container_name: containerName || null,
          project_id: projectId,
        }),
      });
      if (!res.ok) {
        onError(await failureMessage(res, "Could not add the container mapping"));
        return;
      }
      setContainerKey("");
      setContainerName("");
      await refreshMappings();
      onRefresh();
    } finally {
      setBusy(false);
    }
  }

  async function deleteMapping(mapping: WorkspaceMap) {
    if (!source) return;
    const res = await fetch(
      `/api/runtime/sources/${source.id}/workspace-map/${encodeURIComponent(mapping.container_key)}`,
      { method: "DELETE" },
    );
    if (!res.ok) {
      onError(await failureMessage(res, "Could not remove the container mapping"));
      return;
    }
    await refreshMappings();
    onRefresh();
  }

  if (!source) {
    return (
      <Panel title="Source detail" icon={Server} flush>
        <Empty
          label="No source selected."
          hint="Select a row to see its sync health, container mappings and audit trail."
        />
      </Panel>
    );
  }

  const health = source.health;

  return (
    <div className="flex flex-col gap-5">
      <Panel title={source.label} icon={Server} description={source.source_key}>
        <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border">
          <Metric label="Last attempt" value={<RelativeTime value={health?.last_attempt_at} />} />
          <Metric
            label="Last success"
            value={<RelativeTime value={health?.last_success_at ?? source.last_synced_at} />}
          />
          <Metric label="Last failure" value={<RelativeTime value={health?.last_failure_at} />} />
          <Metric
            label="Consecutive failures"
            value={String(health?.consecutive_failures ?? 0)}
            tone={(health?.consecutive_failures ?? 0) > 0 ? "danger" : "default"}
          />
          <Metric label="Cycles" value={formatCompact(health?.total_cycles ?? 0)} />
          <Metric label="Records" value={formatCompact(health?.total_records_ingested ?? 0)} />
        </dl>
        {health?.last_failure_sanitized && (
          <p className="mt-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {health.last_failure_sanitized}
          </p>
        )}
      </Panel>

      <Panel
        title="Container mappings"
        icon={MapPinned}
        description="Which source-side container belongs to which Nexus project."
      >
        <ul className="space-y-2">
          {mappings.map((mapping) => (
            <li
              key={mapping.id}
              className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2"
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-foreground">
                  {mapping.container_name || mapping.container_key}
                </span>
                <span className="block truncate text-xs text-muted-foreground">
                  {mapping.project_id}
                </span>
              </span>
              <IconButton
                label="Remove mapping"
                tone="danger"
                onClick={() => void deleteMapping(mapping)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </IconButton>
            </li>
          ))}
          {mappings.length === 0 && (
            <li className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
              No container mappings yet.
            </li>
          )}
        </ul>
        <form className="mt-3 grid gap-2 border-t border-border/60 pt-3" onSubmit={addMapping}>
          <Input
            placeholder="Container key"
            value={containerKey}
            onChange={(event) => setContainerKey(event.target.value)}
            required
          />
          <Input
            placeholder="Container name (optional)"
            value={containerName}
            onChange={(event) => setContainerName(event.target.value)}
          />
          <SelectMenu
            value={projectId}
            onChange={setProjectId}
            options={projects.map((project) => ({
              value: project.id,
              label: project.display_name,
            }))}
            placeholder="Select a project"
            ariaLabel="Mapped project"
            variant="field"
            size="md"
            className="w-full max-w-none"
          />
          <Button type="submit" variant="secondary" size="md" isLoading={busy}>
            <Plus className="h-3.5 w-3.5" aria-hidden />
            Add mapping
          </Button>
        </form>
      </Panel>

      <Panel
        title="Audit"
        icon={ScrollText}
        description="Credential and configuration changes recorded for this source."
        flush
      >
        {auditEvents.length === 0 ? (
          <Empty label="No audit events recorded." />
        ) : (
          <ul className="divide-y divide-border">
            {auditEvents.map((event) => (
              <li key={event.id} className="flex items-center justify-between gap-2 px-5 py-2.5">
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-foreground">
                    {event.action}
                  </span>
                  {event.username && (
                    <span className="block truncate text-xs text-muted-foreground">
                      {event.username}
                    </span>
                  )}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  <RelativeTime value={event.created_at} />
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function Metric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  tone?: "default" | "danger";
}) {
  return (
    <div className="bg-card px-3 py-2.5">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd
        className={cn(
          "mt-0.5 truncate text-sm font-medium tabular-nums",
          tone === "danger" ? "text-destructive" : "text-foreground",
        )}
      >
        {value}
      </dd>
    </div>
  );
}
