import { useScanRuns } from "../hooks/useProjects";

interface Props {
  projectId: string | null;
}

const statusColors: Record<string, string> = {
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
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

  if (!projectId) return null;

  const runs = data?.runs ?? [];

  return (
    <div className="bg-white rounded-lg shadow border">
      <div className="px-4 py-3 border-b bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-700">
          Scan History
          {runs.length > 0 && (
            <span className="ml-2 text-xs font-normal text-gray-400">
              ({runs.length} run{runs.length !== 1 ? "s" : ""})
            </span>
          )}
        </h3>
      </div>

      {isLoading ? (
        <div className="p-4 text-sm text-gray-400">Loading...</div>
      ) : runs.length === 0 ? (
        <div className="p-4 text-sm text-gray-400 italic">
          No scan runs yet for this project.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50 text-left text-xs text-gray-500 uppercase">
                <th className="px-4 py-2">Date</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Duration</th>
                <th className="px-4 py-2">Jobs Found</th>
                <th className="px-4 py-2">Triggered By</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="border-b hover:bg-gray-50">
                  <td className="px-4 py-2 whitespace-nowrap">
                    {formatDate(run.started_at)}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                        statusColors[run.status] ?? "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {run.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap">
                    {formatDuration(run.started_at, run.completed_at)}
                  </td>
                  <td className="px-4 py-2">{run.job_count}</td>
                  <td className="px-4 py-2 text-gray-500">{run.triggered_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
