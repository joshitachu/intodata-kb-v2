"use client";

import { useState } from "react";
import { RefreshCw, AlertTriangle, ScrollText } from "lucide-react";
import {
  useRuntimeExecution,
  useRuntimeExecutionLogs,
  useRuntimeExecutions,
  useRuntimeFailures,
} from "@/hooks/useRuntimeLogs";
import type {
  RuntimeExecution,
  RuntimeExecutionState,
  RuntimeLogSeverity,
} from "@/types";
import { cn } from "@/lib/utils";

type View = "executions" | "failures";

const STATE_OPTIONS: { value: "" | RuntimeExecutionState; label: string }[] = [
  { value: "", label: "All states" },
  { value: "success", label: "Success" },
  { value: "failed", label: "Failed" },
  { value: "running", label: "Running" },
  { value: "unknown", label: "Unknown" },
];

const SEVERITY_OPTIONS: { value: "" | RuntimeLogSeverity; label: string }[] = [
  { value: "", label: "All severities" },
  { value: "debug", label: "Debug+" },
  { value: "info", label: "Info+" },
  { value: "warning", label: "Warning+" },
  { value: "error", label: "Error only" },
];

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function StateBadge({ state }: { state: RuntimeExecutionState }) {
  const styles: Record<RuntimeExecutionState, string> = {
    success: "bg-green-100 text-green-700",
    failed: "bg-red-100 text-red-700",
    running: "bg-blue-100 text-blue-700",
    unknown: "bg-gray-100 text-gray-600",
  };
  return (
    <span className={cn("px-2 py-0.5 rounded-full text-xs font-medium", styles[state])}>
      {state}
    </span>
  );
}

function SeverityBadge({ severity }: { severity: RuntimeLogSeverity }) {
  const styles: Record<RuntimeLogSeverity, string> = {
    error: "bg-red-100 text-red-700",
    warning: "bg-amber-100 text-amber-700",
    info: "bg-slate-100 text-slate-600",
    debug: "bg-gray-100 text-gray-500",
  };
  return (
    <span
      className={cn(
        "px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide shrink-0",
        styles[severity],
      )}
    >
      {severity}
    </span>
  );
}

export default function RuntimeLogsPage() {
  const [view, setView] = useState<View>("executions");
  const [stateFilter, setStateFilter] = useState<"" | RuntimeExecutionState>("");
  const [jobName, setJobName] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [minSeverity, setMinSeverity] = useState<"" | RuntimeLogSeverity>("");

  const executions = useRuntimeExecutions({
    state: stateFilter || undefined,
    jobName: jobName.trim() || undefined,
  });
  const failures = useRuntimeFailures({});
  const detail = useRuntimeExecution(selectedId, selectedId !== null);
  const logs = useRuntimeExecutionLogs(
    selectedId,
    { minSeverity: minSeverity || undefined, limit: 1000 },
    selectedId !== null,
  );

  function refresh() {
    executions.refetch();
    failures.refetch();
    if (selectedId) {
      detail.refetch();
      logs.refetch();
    }
  }

  return (
    <div className="max-w-7xl mx-auto py-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <ScrollText className="h-6 w-6" />
          Runtime Logs
        </h1>
        <button
          onClick={refresh}
          className="flex items-center gap-2 px-3 py-2 border rounded-md text-sm hover:bg-muted transition-colors"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* View tabs */}
      <div className="border-b">
        <nav className="flex gap-6">
          {(["executions", "failures"] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={cn(
                "pb-2 text-sm font-medium border-b-2 -mb-px capitalize transition-colors",
                view === v
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {v}
            </button>
          ))}
        </nav>
      </div>

      {view === "executions" ? (
        <ExecutionsView
          query={executions}
          stateFilter={stateFilter}
          onStateFilter={setStateFilter}
          jobName={jobName}
          onJobName={setJobName}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      ) : (
        <FailuresView query={failures} selectedId={selectedId} onSelect={setSelectedId} />
      )}

      {selectedId && (
        <LogDetailPanel
          executionId={selectedId}
          detail={detail.data?.execution ?? null}
          detailLoading={detail.isLoading}
          detailError={detail.isError}
          logs={logs}
          minSeverity={minSeverity}
          onMinSeverity={setMinSeverity}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}

function ExecutionsView({
  query,
  stateFilter,
  onStateFilter,
  jobName,
  onJobName,
  selectedId,
  onSelect,
}: {
  query: ReturnType<typeof useRuntimeExecutions>;
  stateFilter: "" | RuntimeExecutionState;
  onStateFilter: (v: "" | RuntimeExecutionState) => void;
  jobName: string;
  onJobName: (v: string) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={stateFilter}
          onChange={(e) => onStateFilter(e.target.value as "" | RuntimeExecutionState)}
          className="px-3 py-2 border rounded-md text-sm bg-background"
          aria-label="Filter by state"
        >
          {STATE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <input
          value={jobName}
          onChange={(e) => onJobName(e.target.value)}
          placeholder="Filter by job name"
          className="px-3 py-2 border rounded-md text-sm bg-background flex-1 min-w-[12rem]"
          aria-label="Filter by job name"
        />
      </div>

      {query.isLoading ? (
        <StatusBox>Loading executions…</StatusBox>
      ) : query.isError ? (
        <StatusBox error>Failed to load executions.</StatusBox>
      ) : !query.data || query.data.executions.length === 0 ? (
        <StatusBox>No executions found.</StatusBox>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-sm table-fixed">
            <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
              <tr>
                <th className="text-left font-medium px-3 py-2 w-[22%]">Execution</th>
                <th className="text-left font-medium px-3 py-2 w-[20%]">Job</th>
                <th className="text-left font-medium px-3 py-2 w-[16%]">Project</th>
                <th className="text-left font-medium px-3 py-2 w-[12%]">State</th>
                <th className="text-left font-medium px-3 py-2 w-[20%]">Started</th>
                <th className="text-right font-medium px-3 py-2 w-[10%]">Duration</th>
              </tr>
            </thead>
            <tbody>
              {query.data.executions.map((e) => (
                <tr
                  key={e.execution_id}
                  onClick={() => onSelect(e.execution_id)}
                  className={cn(
                    "border-t cursor-pointer hover:bg-muted/40 transition-colors",
                    selectedId === e.execution_id && "bg-muted/60",
                  )}
                >
                  <td className="px-3 py-2 font-mono text-xs truncate" title={e.execution_id}>
                    {e.execution_id}
                  </td>
                  <td className="px-3 py-2 truncate" title={e.job_name}>{e.job_name}</td>
                  <td className="px-3 py-2 truncate text-muted-foreground">
                    {e.project_name ?? "—"}
                  </td>
                  <td className="px-3 py-2">
                    <StateBadge state={e.state} />
                  </td>
                  <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
                    {formatTimestamp(e.started_at)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatDuration(e.duration_ms)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function FailuresView({
  query,
  selectedId,
  onSelect,
}: {
  query: ReturnType<typeof useRuntimeFailures>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (query.isLoading) return <StatusBox>Loading failures…</StatusBox>;
  if (query.isError) return <StatusBox error>Failed to load failures.</StatusBox>;
  if (!query.data || query.data.failures.length === 0) {
    return <StatusBox>No failures recorded.</StatusBox>;
  }

  return (
    <div className="border rounded-lg overflow-hidden">
      <table className="w-full text-sm table-fixed">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-2 w-[18%]">Job</th>
            <th className="text-left font-medium px-3 py-2 w-[20%]">Failed at</th>
            <th className="text-left font-medium px-3 py-2 w-[42%]">Error summary</th>
            <th className="text-left font-medium px-3 py-2 w-[20%]">Execution</th>
          </tr>
        </thead>
        <tbody>
          {query.data.failures.map((f) => (
            <tr
              key={f.execution_id}
              onClick={() => onSelect(f.execution_id)}
              className={cn(
                "border-t cursor-pointer hover:bg-muted/40 transition-colors",
                selectedId === f.execution_id && "bg-muted/60",
              )}
            >
              <td className="px-3 py-2 truncate" title={f.job_name}>{f.job_name}</td>
              <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
                {formatTimestamp(f.failed_at)}
              </td>
              <td className="px-3 py-2">
                <span className="flex items-start gap-1.5">
                  <AlertTriangle className="h-3.5 w-3.5 text-red-500 mt-0.5 shrink-0" />
                  <span className="text-red-700">{f.error_summary}</span>
                </span>
              </td>
              <td className="px-3 py-2 font-mono text-xs truncate" title={f.execution_id}>
                {f.execution_id}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LogDetailPanel({
  executionId,
  detail,
  detailLoading,
  detailError,
  logs,
  minSeverity,
  onMinSeverity,
  onClose,
}: {
  executionId: string;
  detail: RuntimeExecution | null;
  detailLoading: boolean;
  detailError: boolean;
  logs: ReturnType<typeof useRuntimeExecutionLogs>;
  minSeverity: "" | RuntimeLogSeverity;
  onMinSeverity: (v: "" | RuntimeLogSeverity) => void;
  onClose: () => void;
}) {
  return (
    <div className="border rounded-lg bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <ScrollText className="h-4 w-4" />
            <span className="font-mono text-xs truncate" title={executionId}>
              {executionId}
            </span>
          </h2>
          {detail && (
            <p className="text-xs text-muted-foreground mt-0.5">
              {detail.job_name}
              {detail.project_name ? ` · ${detail.project_name}` : ""} ·{" "}
              <span className="inline-flex align-middle">
                <StateBadge state={detail.state} />
              </span>{" "}
              · {formatTimestamp(detail.started_at)} · {formatDuration(detail.duration_ms)}
            </p>
          )}
          {detailLoading && <p className="text-xs text-muted-foreground mt-0.5">Loading detail…</p>}
          {detailError && (
            <p className="text-xs text-red-600 mt-0.5">Execution detail unavailable.</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={minSeverity}
            onChange={(e) => onMinSeverity(e.target.value as "" | RuntimeLogSeverity)}
            className="px-2 py-1.5 border rounded-md text-xs bg-background"
            aria-label="Filter logs by minimum severity"
          >
            {SEVERITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <button
            onClick={onClose}
            className="px-2 py-1.5 border rounded-md text-xs hover:bg-muted transition-colors"
          >
            Close
          </button>
        </div>
      </div>

      <div className="p-2">
        {logs.isLoading ? (
          <StatusBox>Loading log lines…</StatusBox>
        ) : logs.isError ? (
          <StatusBox error>Failed to load log lines.</StatusBox>
        ) : !logs.data || logs.data.events.length === 0 ? (
          <StatusBox>No log lines for the selected severity.</StatusBox>
        ) : (
          <ul className="divide-y font-mono text-xs">
            {logs.data.events.map((ev, i) => (
              <li key={`${ev.timestamp}-${i}`} className="flex items-start gap-2 px-2 py-1.5">
                <span className="text-muted-foreground whitespace-nowrap shrink-0">
                  {formatTimestamp(ev.timestamp)}
                </span>
                <SeverityBadge severity={ev.severity} />
                <span className="whitespace-pre-wrap break-words">{ev.message}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function StatusBox({ children, error }: { children: React.ReactNode; error?: boolean }) {
  return (
    <div
      className={cn(
        "border rounded-lg px-4 py-8 text-center text-sm",
        error ? "text-red-600 border-red-200 bg-red-50" : "text-muted-foreground",
      )}
    >
      {children}
    </div>
  );
}
