"use client";

import { useEffect, useState } from "react";
import {
  useRuntimeComponentHotspots,
  useRuntimeErrorSignatures,
  useRuntimeJobReliability,
  useRuntimeJobThroughput,
} from "@/hooks/useRuntimeLogs";
import {
  EntityTable,
  PAGE_SIZE,
  PaginatedTable,
  Panel,
  QueryState,
  formatCompact,
  formatDate,
  formatDuration,
  formatPercent,
} from "./runtime-ui";

// Panels over the SQL-computed rollups. Each is route-scoped: the hook only
// fires where the panel is mounted, so a view never pays for another view's data.

interface ScopeProps {
  scope?: string;
  since?: string;
}

function usePagedScope({ scope, since }: ScopeProps) {
  const [page, setPage] = useState(0);
  useEffect(() => {
    setPage(0);
  }, [scope, since]);
  return { page, setPage, offset: page * PAGE_SIZE };
}

export function RuntimeReliabilityPanel({ scope, since }: ScopeProps) {
  const { page, setPage, offset } = usePagedScope({ scope, since });
  const query = useRuntimeJobReliability({
    projectId: scope,
    since,
    limit: PAGE_SIZE,
    offset,
  });

  return (
    <Panel
      title="Job reliability"
      description="Per-job outcome and duration percentiles for the selected window."
    >
      <QueryState queries={[query]}>
        <PaginatedTable
          rows={query.data?.jobs ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => (
            <EntityTable
              headers={[
                "Job",
                "Runs",
                "Failed",
                "Failure rate",
                "Streak",
                "Never finished",
                "p50",
                "p95",
                "Last run",
              ]}
              rows={rows}
              rowKey={(job) => `${job.project_id}:${job.source_key}:${job.job_name}`}
              numeric={[1, 2, 3, 4, 5, 6, 7]}
              emptyLabel="No runs were recorded in this window."
              cells={(job) => [
                job.job_name,
                job.total_runs.toLocaleString(),
                job.failed_runs.toLocaleString(),
                formatPercent(job.failure_rate),
                job.current_failure_streak.toLocaleString(),
                job.unfinished_runs > 0 ? job.unfinished_runs.toLocaleString() : "-",
                formatDuration(job.p50_duration_ms),
                formatDuration(job.p95_duration_ms),
                formatDate(job.last_run_at),
              ]}
            />
          )}
        />
      </QueryState>
    </Panel>
  );
}

export function RuntimeHotspotsPanel({ scope, since }: ScopeProps) {
  const { page, setPage, offset } = usePagedScope({ scope, since });
  const query = useRuntimeComponentHotspots({
    projectId: scope,
    since,
    limit: PAGE_SIZE,
    offset,
  });

  return (
    <Panel
      title="Component hotspots"
      description="Per-component throughput and duration, measured from run metrics."
    >
      <QueryState queries={[query]}>
        <PaginatedTable
          rows={query.data?.components ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => (
            <EntityTable
              headers={[
                "Component",
                "Type",
                "Flow",
                "Runs",
                "Rows",
                "Rejected",
                "Avg",
                "p95",
              ]}
              rows={rows}
              rowKey={(item) => `${item.project_id}:${item.source_key}:${item.component_key}`}
              numeric={[3, 4, 5, 6, 7]}
              emptyLabel="No component metrics were captured in this window."
              cells={(item) => [
                item.component_key,
                item.component_type ?? "-",
                item.source_connector_type || item.target_connector_type
                  ? `${item.source_connector_type ?? "?"} → ${item.target_connector_type ?? "?"}`
                  : "-",
                item.executions.toLocaleString(),
                formatCompact(item.rows_total),
                item.rows_rejected ? formatCompact(item.rows_rejected) : "-",
                formatDuration(item.avg_duration_ms),
                formatDuration(item.p95_duration_ms),
              ]}
            />
          )}
        />
      </QueryState>
    </Panel>
  );
}

export function RuntimeThroughputPanel({ scope, since }: ScopeProps) {
  const { page, setPage, offset } = usePagedScope({ scope, since });
  const query = useRuntimeJobThroughput({
    projectId: scope,
    since,
    limit: PAGE_SIZE,
    offset,
  });

  return (
    <Panel title="Job throughput" description="Rows moved per job, from component metrics.">
      <QueryState queries={[query]}>
        <PaginatedTable
          rows={query.data?.jobs ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => (
            <EntityTable
              headers={["Job", "Runs measured", "Components", "Rows moved", "Rejected"]}
              rows={rows}
              rowKey={(job) => `${job.project_id}:${job.source_key}:${job.job_name}`}
              numeric={[1, 2, 3, 4]}
              emptyLabel="No throughput metrics were captured in this window."
              cells={(job) => [
                job.job_name,
                job.executions_with_metrics.toLocaleString(),
                job.component_count.toLocaleString(),
                formatCompact(job.rows_total),
                job.rows_rejected ? formatCompact(job.rows_rejected) : "-",
              ]}
            />
          )}
        />
      </QueryState>
    </Panel>
  );
}

export function RuntimeSignaturesPanel({ scope, since }: ScopeProps) {
  const { page, setPage, offset } = usePagedScope({ scope, since });
  const query = useRuntimeErrorSignatures({
    projectId: scope,
    since,
    limit: PAGE_SIZE,
    offset,
  });

  return (
    <Panel
      title="Error signatures"
      description="Distinct faults with how often each recurred, most frequent first."
    >
      <QueryState queries={[query]}>
        <PaginatedTable
          rows={query.data?.signatures ?? []}
          total={query.data?.total ?? 0}
          page={page}
          onPage={setPage}
          render={(rows) => (
            <EntityTable
              headers={["Fault", "Error code", "Exception", "Component", "Seen", "Last seen"]}
              rows={rows}
              rowKey={(item) => `${item.project_id}:${item.signature_hash}`}
              numeric={[4]}
              emptyLabel="No error signatures were recorded in this window."
              cells={(item) => [
                // causal_message is the log's own diagnostic sentence; prefer it
                // over the composed signal join when the backend supplies it.
                item.causal_message || item.summary_text || item.signature_hash.slice(0, 12),
                item.error_code ?? "-",
                item.exception_class ?? "-",
                item.component_name ?? "-",
                item.occurrence_count.toLocaleString(),
                formatDate(item.last_seen_at),
              ]}
            />
          )}
        />
      </QueryState>
    </Panel>
  );
}
