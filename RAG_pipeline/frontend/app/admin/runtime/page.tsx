"use client";

import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import {
  Activity,
  KeyRound,
  MapPinned,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { useUserProjects } from "@/hooks/useUserProjects";
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

function formatTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function sourceStatus(source: RuntimeSource) {
  if (!source.enabled && source.disabled_reason === "auto-disabled") return "auto-disabled";
  if (!source.enabled) return "disabled";
  if (source.status === "failed") return "error";
  return "enabled";
}

export default function AdminRuntimePage() {
  const projects = useUserProjects();
  const [sources, setSources] = useState<RuntimeSource[]>([]);
  const [sourceTypes, setSourceTypes] = useState<SourceType[]>([]);
  const [selected, setSelected] = useState<RuntimeSource | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [rotateFor, setRotateFor] = useState<RuntimeSource | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [sourcesRes, typesRes] = await Promise.all([
        fetch("/api/runtime/sources", { cache: "no-store" }),
        fetch("/api/runtime/source-types", { cache: "no-store" }),
      ]);
      if (!sourcesRes.ok) throw new Error("Failed to load runtime sources");
      if (!typesRes.ok) throw new Error("Failed to load source types");
      const sourcesJson = await sourcesRes.json();
      const typesJson = await typesRes.json();
      setSources(sourcesJson.sources ?? []);
      setSourceTypes(typesJson.source_types ?? []);
      setSelected((current) => {
        if (!current) return null;
        return (sourcesJson.sources ?? []).find((source: RuntimeSource) => source.id === current.id) ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Runtime source request failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const filteredSources = useMemo(() => {
    if (statusFilter === "all") return sources;
    return sources.filter((source) => sourceStatus(source) === statusFilter);
  }, [sources, statusFilter]);

  const autoDisabled = sources.filter((source) => sourceStatus(source) === "auto-disabled");

  async function patchSource(source: RuntimeSource, patch: Partial<RuntimeSource>) {
    const res = await fetch(`/api/runtime/sources/${source.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error("Update failed");
    await refresh();
  }

  async function deleteSource(source: RuntimeSource) {
    if (!window.confirm(`Delete ${source.label}?`)) return;
    const res = await fetch(`/api/runtime/sources/${source.id}`, { method: "DELETE" });
    if (!res.ok) {
      setError("Delete failed");
      return;
    }
    setSelected(null);
    await refresh();
  }

  async function syncNow(source: RuntimeSource) {
    const res = await fetch(`/api/runtime/sources/${source.id}/sync`, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail ?? "Sync failed");
      return;
    }
    await refresh();
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5 py-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          <Activity className="h-6 w-6" />
          Runtime Sources
        </h1>
        <div className="flex items-center gap-2">
          <button className={secondaryButtonClass} onClick={refresh} disabled={loading}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </button>
          <button className={primaryButtonClass} onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4" />
            Add Source
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {autoDisabled.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
          <div className="font-medium text-amber-900">Auto-disabled</div>
          <div className="mt-2 space-y-2">
            {autoDisabled.map((source) => (
              <div key={source.id} className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-amber-800">
                  {source.label}: {source.health?.last_failure_sanitized ?? "sync failure"}
                </span>
                <button className={secondaryButtonClass} onClick={() => patchSource(source, { enabled: true })}>
                  <Play className="h-4 w-4" />
                  Re-enable
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2 border-b pb-3">
        {["all", "enabled", "disabled", "auto-disabled", "error"].map((status) => (
          <button
            key={status}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm",
              statusFilter === status ? "bg-foreground text-background" : "bg-muted text-muted-foreground",
            )}
            onClick={() => setStatusFilter(status)}
          >
            {status}
          </button>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_420px]">
        <section className="overflow-hidden rounded-md border bg-background">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left">
              <tr>
                <th className="px-3 py-2 font-medium">Source</th>
                <th className="px-3 py-2 font-medium">Project</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Last Success</th>
                <th className="px-3 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredSources.map((source) => (
                <tr
                  key={source.id}
                  className={cn(
                    "cursor-pointer border-b hover:bg-muted/40",
                    selected?.id === source.id && "bg-muted",
                  )}
                  onClick={() => setSelected(source)}
                >
                  <td className="px-3 py-2">
                    <div className="font-medium">{source.label}</div>
                    <div className="text-xs text-muted-foreground">{source.source_type}</div>
                  </td>
                  <td className="px-3 py-2">{source.project_id}</td>
                  <td className="px-3 py-2">
                    <StatusBadge status={sourceStatus(source)} />
                  </td>
                  <td className="px-3 py-2">{formatTime(source.health?.last_success_at ?? source.last_synced_at)}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1" onClick={(event) => event.stopPropagation()}>
                      <IconButton label="Sync now" onClick={() => syncNow(source)} disabled={!source.enabled}>
                        <RefreshCw className="h-4 w-4" />
                      </IconButton>
                      <IconButton label="Rotate credentials" onClick={() => setRotateFor(source)}>
                        <KeyRound className="h-4 w-4" />
                      </IconButton>
                      <IconButton
                        label={source.enabled ? "Disable" : "Enable"}
                        onClick={() => patchSource(source, { enabled: !source.enabled })}
                      >
                        {source.enabled ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                      </IconButton>
                      <IconButton label="Delete" onClick={() => deleteSource(source)}>
                        <Trash2 className="h-4 w-4" />
                      </IconButton>
                    </div>
                  </td>
                </tr>
              ))}
              {filteredSources.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-sm text-muted-foreground">
                    No runtime sources.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        <SourceDetail source={selected} projects={projects.data?.projects ?? []} onRefresh={refresh} />
      </div>

      {addOpen && (
        <SourceDialog
          title="Add Source"
          projects={projects.data?.projects ?? []}
          sourceTypes={sourceTypes}
          onClose={() => setAddOpen(false)}
          onSaved={async () => {
            setAddOpen(false);
            await refresh();
          }}
        />
      )}

      {rotateFor && (
        <RotateDialog
          source={rotateFor}
          sourceType={sourceTypes.find((type) => type.provider_key === rotateFor.source_type) ?? null}
          onClose={() => setRotateFor(null)}
          onSaved={async () => {
            setRotateFor(null);
            await refresh();
          }}
        />
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const style =
    status === "enabled"
      ? "bg-emerald-100 text-emerald-700"
      : status === "auto-disabled"
        ? "bg-amber-100 text-amber-800"
        : status === "error"
          ? "bg-red-100 text-red-700"
          : "bg-gray-100 text-gray-700";
  return <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", style)}>{status}</span>;
}

function IconButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex h-8 w-8 items-center justify-center rounded-md border hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  );
}

function SourceDialog({
  title,
  projects,
  sourceTypes,
  onClose,
  onSaved,
}: {
  title: string;
  projects: { id: string; display_name: string }[];
  sourceTypes: SourceType[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [providerKey, setProviderKey] = useState(sourceTypes[0]?.provider_key ?? "");
  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const [label, setLabel] = useState("");
  const [interval, setInterval] = useState(60);
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const selectedType = sourceTypes.find((type) => type.provider_key === providerKey);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const res = await fetch("/api/runtime/sources", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        project_id: projectId,
        provider_key: providerKey,
        label: label || selectedType?.provider_label || providerKey,
        sync_interval_seconds: interval,
        credentials,
      }),
    });
    setCredentials({});
    if (!res.ok) return;
    onSaved();
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form className="space-y-3" onSubmit={submit}>
        <Field label="Type">
          <select className={inputClass} value={providerKey} onChange={(event) => setProviderKey(event.target.value)}>
            {sourceTypes.map((type) => (
              <option key={type.provider_key} value={type.provider_key}>
                {type.provider_label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Project">
          <select className={inputClass} value={projectId} onChange={(event) => setProjectId(event.target.value)}>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.display_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Name">
          <input className={inputClass} value={label} onChange={(event) => setLabel(event.target.value)} />
        </Field>
        <Field label="Interval">
          <input className={inputClass} type="number" min={30} value={interval} onChange={(event) => setInterval(Number(event.target.value))} />
        </Field>
        {selectedType?.credential_schema.map((field) => (
          <Field key={field.name} label={field.label ?? field.name}>
            <input
              className={inputClass}
              type={field.type === "secret" ? "password" : field.type === "url" ? "url" : "text"}
              required={field.required !== false}
              value={credentials[field.name] ?? ""}
              onChange={(event) => setCredentials((prev) => ({ ...prev, [field.name]: event.target.value }))}
            />
          </Field>
        ))}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className={secondaryButtonClass} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={primaryButtonClass}>
            Save
          </button>
        </div>
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
  source: RuntimeSource;
  sourceType: SourceType | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [credentials, setCredentials] = useState<Record<string, string>>({});

  async function submit(event: FormEvent) {
    event.preventDefault();
    const res = await fetch(`/api/runtime/sources/${source.id}/credentials`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ credentials }),
    });
    setCredentials({});
    if (!res.ok) return;
    onSaved();
  }

  return (
    <Modal title={`Rotate ${source.label}`} onClose={onClose}>
      <form className="space-y-3" onSubmit={submit}>
        {(sourceType?.credential_schema ?? []).map((field) => (
          <Field key={field.name} label={field.label ?? field.name}>
            <input
              className={inputClass}
              type={field.type === "secret" ? "password" : field.type === "url" ? "url" : "text"}
              required={field.required !== false}
              value={credentials[field.name] ?? ""}
              onChange={(event) => setCredentials((prev) => ({ ...prev, [field.name]: event.target.value }))}
            />
          </Field>
        ))}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className={secondaryButtonClass} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={primaryButtonClass}>
            <RotateCcw className="h-4 w-4" />
            Rotate
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SourceDetail({
  source,
  projects,
  onRefresh,
}: {
  source: RuntimeSource | null;
  projects: { id: string; display_name: string }[];
  onRefresh: () => void;
}) {
  const [mappings, setMappings] = useState<WorkspaceMap[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [containerKey, setContainerKey] = useState("");
  const [containerName, setContainerName] = useState("");
  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");

  async function refreshMappings() {
    if (!source) return;
    const res = await fetch(`/api/runtime/sources/${source.id}/workspace-map`, { cache: "no-store" });
    if (!res.ok) return;
    const body = await res.json();
    setMappings(body.mappings ?? []);
  }

  async function refreshAudit() {
    if (!source) return;
    const res = await fetch(`/api/runtime/audit?source_id=${encodeURIComponent(source.id)}`, { cache: "no-store" });
    if (!res.ok) return;
    const body = await res.json();
    setAuditEvents(body.events ?? []);
  }

  useEffect(() => {
    setProjectId(projects[0]?.id ?? "");
    refreshMappings();
    refreshAudit();
  }, [source?.id, projects.length]);

  async function addMapping(event: FormEvent) {
    event.preventDefault();
    if (!source) return;
    const res = await fetch(`/api/runtime/sources/${source.id}/workspace-map`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ container_key: containerKey, container_name: containerName || null, project_id: projectId }),
    });
    if (res.ok) {
      setContainerKey("");
      setContainerName("");
      await refreshMappings();
      onRefresh();
    }
  }

  async function deleteMapping(mapping: WorkspaceMap) {
    if (!source) return;
    const res = await fetch(`/api/runtime/sources/${source.id}/workspace-map/${encodeURIComponent(mapping.container_key)}`, {
      method: "DELETE",
    });
    if (res.ok) {
      await refreshMappings();
      onRefresh();
    }
  }

  if (!source) {
    return <aside className="rounded-md border p-4 text-sm text-muted-foreground">Select a source.</aside>;
  }

  return (
    <aside className="space-y-4 rounded-md border p-4">
      <div>
        <div className="text-lg font-semibold">{source.label}</div>
        <div className="text-sm text-muted-foreground">{source.source_key}</div>
      </div>
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <Metric label="Attempt" value={formatTime(source.health?.last_attempt_at)} />
        <Metric label="Success" value={formatTime(source.health?.last_success_at ?? source.last_synced_at)} />
        <Metric label="Failure" value={formatTime(source.health?.last_failure_at)} />
        <Metric label="Failures" value={String(source.health?.consecutive_failures ?? 0)} />
        <Metric label="Cycles" value={String(source.health?.total_cycles ?? 0)} />
        <Metric label="Records" value={String(source.health?.total_records_ingested ?? 0)} />
      </dl>
      {source.health?.last_failure_sanitized && (
        <div className="rounded-md border border-red-200 bg-red-50 p-2 text-sm text-red-700">
          {source.health.last_failure_sanitized}
        </div>
      )}
      <section className="space-y-3 border-t pt-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <MapPinned className="h-4 w-4" />
          Container Mappings
        </h2>
        <div className="space-y-2">
          {mappings.map((mapping) => (
            <div key={mapping.id} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
              <div>
                <div className="font-medium">{mapping.container_name || mapping.container_key}</div>
                <div className="text-xs text-muted-foreground">{mapping.project_id}</div>
              </div>
              <IconButton label="Remove mapping" onClick={() => deleteMapping(mapping)}>
                <Trash2 className="h-4 w-4" />
              </IconButton>
            </div>
          ))}
          {mappings.length === 0 && <div className="text-sm text-muted-foreground">No mappings.</div>}
        </div>
        <form className="grid gap-2" onSubmit={addMapping}>
          <input className={inputClass} placeholder="container key" value={containerKey} onChange={(event) => setContainerKey(event.target.value)} required />
          <input className={inputClass} placeholder="container name" value={containerName} onChange={(event) => setContainerName(event.target.value)} />
          <select className={inputClass} value={projectId} onChange={(event) => setProjectId(event.target.value)}>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.display_name}
              </option>
            ))}
          </select>
          <button className={secondaryButtonClass} type="submit">
            <Plus className="h-4 w-4" />
            Add Mapping
          </button>
        </form>
      </section>
      <section className="space-y-3 border-t pt-4">
        <h2 className="text-sm font-semibold">Audit</h2>
        <div className="space-y-2">
          {auditEvents.map((event) => (
            <div key={event.id} className="rounded-md border px-3 py-2 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{event.action}</span>
                <span className="text-xs text-muted-foreground">{formatTime(event.created_at)}</span>
              </div>
              {event.username && <div className="text-xs text-muted-foreground">{event.username}</div>}
            </div>
          ))}
          {auditEvents.length === 0 && <div className="text-sm text-muted-foreground">No audit events.</div>}
        </div>
      </section>
    </aside>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate font-medium">{value}</dd>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-sm">
      <span className="font-medium">{label}</span>
      {children}
    </label>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/35 p-4">
      <div className="w-full max-w-lg rounded-md border bg-background p-4 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <IconButton label="Close" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </div>
        {children}
      </div>
    </div>
  );
}

const primaryButtonClass =
  "inline-flex items-center gap-2 rounded-md bg-foreground px-3 py-2 text-sm font-medium text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";

const secondaryButtonClass =
  "inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50";

const inputClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-foreground";
