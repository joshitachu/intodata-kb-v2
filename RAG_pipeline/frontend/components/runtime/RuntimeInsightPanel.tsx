"use client";

import { AlertTriangle, CheckCircle, Clock, HelpCircle, Info } from "lucide-react";
import {
  useRuntimeExecutionSummary,
  useRuntimeExecutionTimeline,
  useRuntimeIncident,
  useRuntimeIncidentEvidence,
  useRuntimeSimilarFailures,
} from "@/hooks/useRuntimeLogs";
import type {
  RuntimeFailureEstateContext,
  RuntimeIncidentEvidence,
  RuntimeSimilarIncident,
  RuntimeTimelineEvent,
} from "@/types";
import { cn } from "@/lib/utils";
import { CauseOriginBadge, PatternIdTag, SeverityChip } from "./runtime-ui";

interface RuntimeInsightPanelProps {
  executionId: string;
}

export function RuntimeInsightPanel({ executionId }: RuntimeInsightPanelProps) {
  const summary = useRuntimeExecutionSummary(executionId);
  const incidentId = summary.data?.summary.incident_id ?? null;
  const hasIncident = summary.data?.summary.has_incident ?? false;

  const timeline = useRuntimeExecutionTimeline(executionId, {}, summary.isSuccess);
  const incident = useRuntimeIncident(incidentId, summary.isSuccess && hasIncident);
  const evidence = useRuntimeIncidentEvidence(
    incidentId,
    { limit: 5 },
    incident.isSuccess,
  );
  // Registered after evidence so its fetch fires after evidence in hook-order.
  const similar = useRuntimeSimilarFailures(incidentId, { limit: 5 }, incident.isSuccess);

  if (summary.isLoading) {
    return <InsightSection>Loading smart summary…</InsightSection>;
  }

  if (summary.isError) {
    const status = (summary.error as Error).message.includes("404") ? 404 : 0;
    if (status === 404) {
      return (
        <InsightSection muted>No smart summary available for this execution.</InsightSection>
      );
    }
    return (
      <InsightSection error>Smart summary unavailable — try refreshing.</InsightSection>
    );
  }

  if (!summary.data) return null;

  const s = summary.data.summary;

  return (
    <div className="border rounded-lg divide-y text-sm">
      {/* Summary header */}
      <div className="px-4 py-3 space-y-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {s.warning_count > 0 && (
            <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
              <AlertTriangle className="h-3.5 w-3.5" />
              {s.warning_count} warning{s.warning_count !== 1 ? "s" : ""}
            </span>
          )}
          {s.error_count > 0 && (
            <span className="flex items-center gap-1 text-destructive">
              <AlertTriangle className="h-3.5 w-3.5" />
              {s.error_count} error{s.error_count !== 1 ? "s" : ""}
            </span>
          )}
          {s.duration_ms != null && (
            <span className="flex items-center gap-1">
              <Clock className="h-3.5 w-3.5" />
              {formatDuration(s.duration_ms)}
            </span>
          )}
        </div>
        {s.summary_text && (
          <p className="text-sm">{s.summary_text}</p>
        )}
        {/* The log's own diagnostic sentence. It states the fault in the emitting
            runtime's words, so it outranks any composed summary above. */}
        {s.causal_message && (
          <pre className="min-w-0 whitespace-pre-wrap break-words rounded-md bg-muted/60 px-2.5 py-2 font-mono text-xs leading-relaxed text-foreground">
            {s.causal_message}
          </pre>
        )}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-1 text-xs mt-1">
          {s.failed_component && (
            <FactRow label="Failed component" value={s.failed_component} mono />
          )}
          {s.primary_error_code && (
            <FactRow label="Error code" value={s.primary_error_code} mono />
          )}
          {s.primary_exception_class && (
            <FactRow label="Exception class" value={s.primary_exception_class} mono />
          )}
          {s.first_error_at && (
            <FactRow label="First error at" value={formatTs(s.first_error_at)} />
          )}
          {s.final_error_at && s.final_error_at !== s.first_error_at && (
            <FactRow label="Final error at" value={formatTs(s.final_error_at)} />
          )}
          {s.first_warning_at && (
            <FactRow label="First warning at" value={formatTs(s.first_warning_at)} />
          )}
        </div>
      </div>

      <EstateContextSection context={s.estate_context} />

      {/* Incident / likely cause */}
      {summary.isSuccess && !hasIncident && (
        <div className="px-4 py-3 text-xs text-muted-foreground flex items-center gap-1.5">
          <Info className="h-3.5 w-3.5 shrink-0" />
          No incident was derived for this execution.
        </div>
      )}

      {hasIncident && (
        <>
          {incident.isLoading && (
            <InsightSection small>Loading incident…</InsightSection>
          )}
          {incident.isError && (
            <InsightSection small muted>Incident data unavailable.</InsightSection>
          )}
          {incident.data && (
            <div className="px-4 py-3 space-y-2">
              {incident.data.incident.symptom_summary && (
                <p className="text-xs text-muted-foreground">
                  {incident.data.incident.symptom_summary}
                </p>
              )}
              {incident.data.incident.causal_message && (
                <pre className="min-w-0 whitespace-pre-wrap break-words rounded-md bg-muted/60 px-2.5 py-2 font-mono text-xs leading-relaxed text-foreground">
                  {incident.data.incident.causal_message}
                </pre>
              )}
              {incident.data.incident.root_cause_candidate && (
                <div className="space-y-0.5">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    Likely Cause
                  </p>
                  <p className="text-sm flex items-start gap-1.5">
                    <HelpCircle className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
                    <span>
                      {incident.data.incident.root_cause_candidate}
                      {incident.data.incident.confidence && (
                        <span className="ml-1.5 text-xs text-muted-foreground">
                          (
                          {incident.data.incident.confidence === "low"
                            ? // "low" is the catalog's fallback tier: no cause was
                              // derived. Saying "probable" would overstate it.
                              "low confidence — no cause was derived from the available evidence"
                            : `${incident.data.incident.confidence} confidence — probable, not confirmed`}
                          )
                        </span>
                      )}
                    </span>
                  </p>
                  {/* Who produced the cause, and which rule matched. Always
                      stated: quiet for the rule engine, loud for an LLM. */}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-5">
                    <CauseOriginBadge origin={incident.data.incident.cause_origin} />
                    <PatternIdTag patternId={incident.data.incident.pattern_id} />
                  </div>
                </div>
              )}
              {incident.data.incident.next_checks.length > 0 && (
                <div className="space-y-0.5">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    Next Checks
                  </p>
                  <ul className="space-y-0.5">
                    {incident.data.incident.next_checks.map((check, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-sm">
                        <CheckCircle className="h-3.5 w-3.5 text-primary mt-0.5 shrink-0" />
                        {check}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Recurrence */}
          {similar.data && <SimilarSection data={similar.data} />}

          {/* Evidence */}
          {incident.isSuccess && (
            <>
              {evidence.isLoading && (
                <InsightSection small>Loading evidence…</InsightSection>
              )}
              {evidence.isError && (
                <InsightSection small muted>Evidence data unavailable.</InsightSection>
              )}
              {evidence.data && evidence.data.evidence.length === 0 && (
                <InsightSection small muted>
                  No evidence excerpts were linked to this incident.
                </InsightSection>
              )}
              {evidence.data && evidence.data.evidence.length > 0 && (
                <EvidenceTable evidence={evidence.data.evidence} />
              )}
            </>
          )}
        </>
      )}

      {/* Timeline */}
      {timeline.isLoading && <InsightSection small>Loading timeline…</InsightSection>}
      {timeline.isError && (
        <InsightSection small muted>Timeline data unavailable.</InsightSection>
      )}
      {timeline.data && timeline.data.events.length === 0 && (
        <InsightSection small muted>
          No timeline events match the current filters.
        </InsightSection>
      )}
      {timeline.data && timeline.data.events.length > 0 && (
        <TimelineTable events={timeline.data.events} />
      )}
    </div>
  );
}

function EstateContextSection({ context }: { context: RuntimeFailureEstateContext }) {
  const sourceLabel = context.source_label || context.source_key || "Unknown source";
  const jobLabel = context.job_display_name || context.job_name || context.job_id || "Unknown job";
  const planLabel = context.plan_display_name || context.plan_name || context.plan_id || "Not reported";

  return (
    <div className="px-4 py-3 space-y-2">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
        Runtime Context
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1.5 text-xs">
        <FactRow
          label="Source"
          value={`${sourceLabel}${context.source_is_demo ? " (demo)" : ""}`}
          mono={!context.source_label}
        />
        <FactRow label="Source status" value={context.source_status || "Unknown"} />
        <FactRow
          label="Fresh as of"
          value={
            context.source_last_synced_at
              ? formatTs(context.source_last_synced_at)
              : "Not reported"
          }
        />
        <FactRow
          label="Environment"
          value={context.environment_name || context.environment_id || "Unknown"}
        />
        <FactRow
          label="Workspace"
          value={context.workspace_name || context.workspace_id || "Unknown"}
        />
        <FactRow
          label="Engine"
          value={formatEngine(context)}
          mono={!context.engine_name && Boolean(context.engine_id)}
        />
        <FactRow label="Job" value={jobLabel} mono={!context.job_display_name} />
        <FactRow label="Job run" value={context.job_run_id || "Not reported"} mono />
        <FactRow label="Plan" value={planLabel} mono={!context.plan_display_name} />
        <FactRow label="Plan run" value={context.plan_run_id || "Not reported"} mono />
      </div>
    </div>
  );
}

function formatEngine(context: RuntimeFailureEstateContext): string {
  const label = context.engine_name || context.engine_id || "Unknown";
  const details = [context.engine_status, context.engine_version].filter(Boolean);
  return details.length > 0 ? `${label} (${details.join(", ")})` : label;
}

function RecurringBadge({ isRecurring, count }: { isRecurring: boolean; count: number }) {
  if (isRecurring) {
    return (
      <span className="inline-flex items-center rounded border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400">
        Recurring · {count}×
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      First occurrence
    </span>
  );
}

function SimilarTable({ incidents }: { incidents: RuntimeSimilarIncident[] }) {
  return (
    <table className="w-full text-xs border-collapse mt-2">
      <thead>
        <tr className="border-b border-border text-left text-muted-foreground">
          <th className="py-1 pr-3 font-medium">When</th>
          <th className="py-1 pr-3 font-medium">Component</th>
          <th className="py-1 font-medium">Likely cause</th>
        </tr>
      </thead>
      <tbody>
        {incidents.map((inc) => (
          <tr key={inc.incident_id} className="border-b border-border/70 align-top">
            <td className="py-1 pr-3 text-muted-foreground whitespace-nowrap">
              {inc.first_bad_at ? new Date(inc.first_bad_at).toLocaleDateString() : "—"}
            </td>
            <td className="py-1 pr-3">{inc.job_name ?? "—"}</td>
            <td className="py-1 text-muted-foreground">{inc.root_cause_candidate ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SimilarSection({ data }: { data: import("@/types").IncidentSimilarResponse }) {
  const { recurrence, similar_incidents } = data;

  return (
    <div className="px-4 py-3 space-y-2 border-t">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
        Recurrence
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <RecurringBadge isRecurring={recurrence.is_recurring} count={recurrence.occurrence_count} />
        {recurrence.first_seen_at && (
          <span className="text-xs text-muted-foreground">
            First seen: {new Date(recurrence.first_seen_at).toLocaleDateString()}
          </span>
        )}
        {recurrence.is_recurring && recurrence.last_seen_at && (
          <span className="text-xs text-muted-foreground">
            Last seen: {new Date(recurrence.last_seen_at).toLocaleDateString()}
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{recurrence.alert_reason}</p>
      {similar_incidents.length > 0 ? (
        <SimilarTable incidents={similar_incidents} />
      ) : (
        <p className="text-xs text-muted-foreground">
          No other matching failures found in this project.
        </p>
      )}
      {recurrence.dedupe_key && (
        <p className="text-xs text-muted-foreground font-mono">
          fingerprint: {recurrence.dedupe_key}
        </p>
      )}
    </div>
  );
}

function EvidenceTable({ evidence }: { evidence: RuntimeIncidentEvidence[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs table-fixed">
        <thead className="bg-muted/40 text-[10px] uppercase text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-1.5 w-[22%]">Timestamp</th>
            <th className="text-left font-medium px-3 py-1.5 w-[10%]">Sev</th>
            <th className="text-left font-medium px-3 py-1.5 w-[14%]">Role</th>
            <th className="text-left font-medium px-3 py-1.5">Excerpt</th>
          </tr>
        </thead>
        <tbody>
          {evidence.map((ev) => (
            <tr key={ev.log_event_id} className="border-t">
              <td className="px-3 py-1.5 text-muted-foreground whitespace-nowrap">
                {formatTs(ev.timestamp)}
              </td>
              <td className="px-3 py-1.5">
                <SeverityChip severity={ev.severity} />
              </td>
              <td className="px-3 py-1.5 text-muted-foreground truncate">
                {ev.evidence_role ?? "—"}
              </td>
              <td className="px-3 py-1.5 font-mono truncate" title={ev.excerpt ?? ""}>
                {ev.excerpt ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TimelineTable({ events }: { events: RuntimeTimelineEvent[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs table-fixed">
        <thead className="bg-muted/40 text-[10px] uppercase text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-1.5 w-[20%]">Timestamp</th>
            <th className="text-left font-medium px-3 py-1.5 w-[9%]">Sev</th>
            <th className="text-left font-medium px-3 py-1.5 w-[16%]">Component</th>
            <th className="text-left font-medium px-3 py-1.5 w-[11%]">Error code</th>
            <th className="text-left font-medium px-3 py-1.5">Signal</th>
          </tr>
        </thead>
        <tbody>
          {events.map((ev) => (
            <tr key={ev.log_event_id} className="border-t">
              <td className="px-3 py-1.5 text-muted-foreground whitespace-nowrap">
                {formatTs(ev.timestamp)}
              </td>
              <td className="px-3 py-1.5">
                <SeverityChip severity={ev.severity} />
              </td>
              <td className="px-3 py-1.5 font-mono truncate text-[10px]">
                {ev.component_name ?? "—"}
              </td>
              <td className="px-3 py-1.5 font-mono truncate text-[10px]">
                {ev.error_code ?? "—"}
              </td>
              <td className="px-3 py-1.5 truncate font-mono text-[10px]" title={ev.signal}>
                {ev.signal}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FactRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <span className="text-muted-foreground">{label}: </span>
      <span className={cn(mono && "font-mono")}>{value}</span>
    </div>
  );
}

function InsightSection({
  children,
  error,
  muted,
  small,
}: {
  children: React.ReactNode;
  error?: boolean;
  muted?: boolean;
  small?: boolean;
}) {
  return (
    <div
      className={cn(
        "px-4 text-sm",
        small ? "py-2" : "py-3",
        error && "text-destructive",
        !error && muted && "text-muted-foreground",
      )}
    >
      {children}
    </div>
  );
}

function formatTs(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
