import { useScanRuns, useScanStatus } from "../hooks/useProjects";

interface Props {
  projectId: string | null;
}

const statusColors: Record<string, string> = {
  running: "border-accent-primary/40 text-accent-primary",
  completed: "border-[#a0e06f]/40 text-[#a0e06f]",
  failed: "border-red-400/40 text-red-400",
};

function formatDuration(start: string, end: string | null): string {
  if (!end) return "running...";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ScanHistory({ projectId }: Props) {
  const { data, isLoading } = useScanRuns(projectId);
  const { data: status } = useScanStatus(projectId);

  if (!projectId) return null;

  const runs = data?.runs ?? [];
  const warningsCount = status?.warnings_count ?? 0;
  const jobDelta = status?.job_delta ?? null;
  // Find the latest completed run — that's the one the /status warnings apply to.
  const latestCompleted = runs.find((r) => r.status === "completed");
  const latestCompletedId = latestCompleted?.id ?? null;

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
        <h3 className="text-sm font-mono text-slate-400">
          Scan History
          {runs.length > 0 && (
            <span className="ml-2 text-xs text-slate-600">
              ({runs.length} run{runs.length !== 1 ? "s" : ""})
            </span>
          )}
        </h3>
      </div>

      {isLoading ? (
        <div className="p-6 text-sm text-slate-500">Loading...</div>
      ) : runs.length === 0 ? (
        <div className="p-6 text-sm text-slate-500 font-mono">
          No scan runs yet for this project.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.04] text-left font-mono text-xs text-slate-600 uppercase tracking-wider">
                <th className="px-4 py-2.5 font-medium">Date</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Duration</th>
                <th className="px-4 py-2.5 font-medium">Components Found</th>
                <th className="px-4 py-2.5 font-medium">Warnings</th>
                <th className="px-4 py-2.5 font-medium">Triggered By</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const isLatestCompleted = run.id === latestCompletedId;
                return (
                  <tr key={run.id} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]">
                    <td className="px-4 py-2.5 whitespace-nowrap text-slate-400">
                      {formatDate(run.started_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`status-chip ${
                          statusColors[run.status] ?? "border-slate-500/40 text-slate-500"
                        }`}
                      >
                        {run.status.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap text-slate-400 font-mono">
                      {formatDuration(run.started_at, run.completed_at)}
                    </td>
                    <td className="px-4 py-2.5 text-slate-400 font-mono">
                      {run.job_count}
                      {isLatestCompleted && jobDelta !== null && jobDelta !== 0 && (
                        <span
                          className={`ml-2 text-xs ${
                            jobDelta > 0 ? "text-[#a0e06f]" : "text-red-400"
                          }`}
                          title={`${jobDelta > 0 ? "+" : ""}${jobDelta} vs previous completed scan`}
                        >
                          {jobDelta > 0 ? "+" : ""}
                          {jobDelta}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 font-mono">
                      {isLatestCompleted && warningsCount > 0 ? (
                        <span
                          className="status-chip border-yellow-400/40 text-yellow-300"
                          title={`${warningsCount} file(s) failed to parse in the latest scan`}
                        >
                          ! {warningsCount}
                        </span>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-slate-500">{run.triggered_by}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
