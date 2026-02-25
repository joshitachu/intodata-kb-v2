import { useGenerateRuns } from "../hooks/useProjects";
import type { GenerateRun } from "../types";

interface Props {
  projectId: string | null;
  onViewLog: (run: GenerateRun) => void;
}

const statusColors: Record<string, string> = {
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

function formatDuration(ms: number | null): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem}s`;
}

function formatTokens(n: number | null): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatCost(usd: number | null): string {
  if (usd == null) return "-";
  return `$${usd.toFixed(4)}`;
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

export default function RunHistory({ projectId, onViewLog }: Props) {
  const { data, isLoading } = useGenerateRuns(projectId);

  if (!projectId) return null;

  const runs = data?.runs ?? [];

  return (
    <div className="bg-white rounded-lg shadow border">
      <div className="px-4 py-3 border-b bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-700">
          Generation History
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
          No generation runs yet for this project.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50 text-left text-xs text-gray-500 uppercase">
                <th className="px-4 py-2">Date</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Duration</th>
                <th className="px-4 py-2">Tokens In</th>
                <th className="px-4 py-2">Tokens Out</th>
                <th className="px-4 py-2">Cost</th>
                <th className="px-4 py-2">Docs</th>
                <th className="px-4 py-2">Actions</th>
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
                    {formatDuration(run.duration_ms)}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap">
                    {formatTokens(run.input_tokens)}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap">
                    {formatTokens(run.output_tokens)}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap font-mono">
                    {formatCost(run.cost_usd)}
                  </td>
                  <td className="px-4 py-2">{run.doc_count}</td>
                  <td className="px-4 py-2">
                    {run.log_file && (
                      <button
                        className="text-xs text-blue-600 hover:text-blue-800 hover:underline"
                        onClick={() => onViewLog(run)}
                      >
                        View Log
                      </button>
                    )}
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
